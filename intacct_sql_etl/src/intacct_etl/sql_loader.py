"""SQL Server load via injected DB connection (specs/04). Procs are black boxes.

The connection is any DB-API-like object exposing cursor()/commit()/rollback(); the cursor
exposes execute(sql, params) and close(). Real wiring uses pyodbc (kept out of import path so
unit tests need no driver). All targets MUST be test_-prefixed (defense-in-depth vs
prod_write_guard) and every interpolated identifier is validated (CR-19).
"""
from __future__ import annotations

import time
from contextlib import closing
from dataclasses import dataclass
from typing import Callable

from . import transform
from .config import assert_test_sql_targets, safe_identifier
from .pagination import DEFAULT_PAGE_SIZE


@dataclass
class LoaderMetrics:
    """Per-loader (per-environment) load metrics for the audit trail (specs/12)."""
    gl_pages: int = 0
    ap_pages: int = 0
    gl_rows_affected: int = 0
    ap_rows_affected: int = 0
    gl_encode_s: float = 0.0
    ap_encode_s: float = 0.0
    gl_db_s: float = 0.0
    ap_db_s: float = 0.0
    wipe_s: float = 0.0
    reconcile_s: float = 0.0

    def record_page(self, kind: str, *, encode_s: float, db_s: float, affected: int) -> None:
        setattr(self, f"{kind}_pages", getattr(self, f"{kind}_pages") + 1)
        setattr(self, f"{kind}_encode_s", getattr(self, f"{kind}_encode_s") + encode_s)
        setattr(self, f"{kind}_db_s", getattr(self, f"{kind}_db_s") + db_s)
        setattr(self, f"{kind}_rows_affected",
                getattr(self, f"{kind}_rows_affected") + affected)


class SqlLoader:
    def __init__(self, connection, sql_cfg: dict, *, require_test_targets: bool = True,
                 clock: Callable[[], float] = time.monotonic):
        self.conn = connection
        self.cfg = sql_cfg
        self.clock = clock
        self.metrics = LoaderMetrics()
        if require_test_targets:
            # CR-09: single shared guard (no duplicated copy here)
            assert_test_sql_targets(sql_cfg)

    def _ident(self, key: str) -> str:
        """Fetch a configured table/proc/field name and validate it as a safe identifier."""
        return safe_identifier(str(self.cfg[key]))

    def _exec_proc_json(self, proc_name: str, json_str: str) -> int:
        """Execute the bulk proc with the page bound as a parameter. Returns rows affected
        (cursor.rowcount; <0 means the proc reported none) for the audit trail."""
        proc = safe_identifier(proc_name)
        with closing(self.conn.cursor()) as cur:
            # Bound parameter — JSON is NOT interpolated into the SQL text (no quote escaping).
            cur.execute(f"EXEC {proc} ?", (json_str,))
            return max(0, getattr(cur, "rowcount", 0) or 0)

    def _load_records(self, proc_key: str, records: list[dict], page_size: int,
                      commit: bool, kind: str) -> int:
        proc = self.cfg[proc_key]
        loaded = 0
        for i in range(0, len(records), page_size):
            chunk = records[i:i + page_size]
            t0 = self.clock()
            payload = transform.encode_page(chunk)          # Transform step (specs/12)
            t1 = self.clock()
            affected = self._exec_proc_json(proc, payload)  # Load step (specs/12)
            t2 = self.clock()
            self.metrics.record_page(kind, encode_s=t1 - t0, db_s=t2 - t1, affected=affected)
            loaded += len(chunk)
        if loaded and commit:
            self.conn.commit()
        return loaded

    def load_gl_records(self, records: list[dict], page_size: int = DEFAULT_PAGE_SIZE,
                        commit: bool = True) -> int:
        return self._load_records("proc_add_gl", records, page_size, commit, "gl")

    def load_ap_records(self, records: list[dict], page_size: int = DEFAULT_PAGE_SIZE,
                        commit: bool = True) -> int:
        return self._load_records("proc_add_ap", records, page_size, commit, "ap")

    # -- W3: destination wipe + reconcile (all identifiers from config, all validated) --

    def wipe_window(self, start, end, *, commit: bool = True) -> None:
        """Delete the refresh window before reload (specs/04 FR-LOAD-1).

        GL: windowed on the configured delete date field. AP: full reload by default
        (`apbill_full_reload`), else windowed on `apbill_delete_date_field`. Table/field
        names come entirely from config (validated); values are bound parameters.
        """
        gl_table = self._ident("gl_table")
        gl_field = safe_identifier(self.cfg.get("gl_delete_date_field", "Entry_Date"))
        t0 = self.clock()
        with closing(self.conn.cursor()) as cur:
            cur.execute(
                f"DELETE FROM {gl_table} WHERE {gl_field} >= ? AND {gl_field} <= ?",
                (start, end),
            )
            ap_table = self._ident("apbill_table")
            if self.cfg.get("apbill_full_reload", True):
                cur.execute(f"DELETE FROM {ap_table}")
            else:
                ap_field = safe_identifier(self.cfg.get("apbill_delete_date_field", "WHENPOSTED"))
                cur.execute(
                    f"DELETE FROM {ap_table} WHERE {ap_field} >= ? AND {ap_field} <= ?",
                    (start, end),
                )
        self.metrics.wipe_s += self.clock() - t0
        if commit:
            self.conn.commit()

    def run_reconcile(self, start, end, *, commit: bool = True) -> None:
        """Post-load reconcile: sync AP links then update vendor info (specs/04 FR-LOAD-3).

        Order is fixed; proc names come from config (validated); dates are bound parameters.
        """
        sync = self._ident("proc_sync_links")
        vendor = self._ident("proc_update_vendor")
        t0 = self.clock()
        with closing(self.conn.cursor()) as cur:
            cur.execute(f"EXEC {sync} ?, ?", (start, end))
            cur.execute(f"EXEC {vendor} ?, ?", (start, end))
        self.metrics.reconcile_s += self.clock() - t0
        if commit:
            self.conn.commit()
