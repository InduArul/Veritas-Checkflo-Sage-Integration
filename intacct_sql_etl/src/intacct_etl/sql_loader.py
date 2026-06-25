"""SQL Server load via injected DB connection (specs/04). Procs are black boxes.

The connection is any DB-API-like object exposing cursor()/commit()/rollback(); the cursor
exposes execute(sql, params) and close(). Real wiring uses pyodbc (kept out of import path so
unit tests need no driver). All targets MUST be test_-prefixed (defense-in-depth vs
prod_write_guard) and every interpolated identifier is validated (CR-19).
"""
from __future__ import annotations

from contextlib import closing

from . import transform
from .config import assert_test_sql_targets, safe_identifier
from .pagination import DEFAULT_PAGE_SIZE


class SqlLoader:
    def __init__(self, connection, sql_cfg: dict, *, require_test_targets: bool = True):
        self.conn = connection
        self.cfg = sql_cfg
        if require_test_targets:
            # CR-09: single shared guard (no duplicated copy here)
            assert_test_sql_targets(sql_cfg)

    def _ident(self, key: str) -> str:
        """Fetch a configured table/proc/field name and validate it as a safe identifier."""
        return safe_identifier(str(self.cfg[key]))

    def _exec_proc_json(self, proc_name: str, json_str: str) -> None:
        proc = safe_identifier(proc_name)
        with closing(self.conn.cursor()) as cur:
            # Bound parameter — JSON is NOT interpolated into the SQL text (no quote escaping).
            cur.execute(f"EXEC {proc} ?", (json_str,))

    def _load_records(self, proc_key: str, records: list[dict], page_size: int,
                      commit: bool = True) -> int:
        proc = self.cfg[proc_key]
        loaded = 0
        for i in range(0, len(records), page_size):
            chunk = records[i:i + page_size]
            self._exec_proc_json(proc, transform.encode_page(chunk))
            loaded += len(chunk)
        if loaded and commit:
            self.conn.commit()
        return loaded

    def load_gl_records(self, records: list[dict], page_size: int = DEFAULT_PAGE_SIZE,
                        commit: bool = True) -> int:
        return self._load_records("proc_add_gl", records, page_size, commit)

    def load_ap_records(self, records: list[dict], page_size: int = DEFAULT_PAGE_SIZE,
                        commit: bool = True) -> int:
        return self._load_records("proc_add_ap", records, page_size, commit)

    # -- W3: destination wipe + reconcile (all identifiers from config, all validated) --

    def wipe_window(self, start, end, *, commit: bool = True) -> None:
        """Delete the refresh window before reload (specs/04 FR-LOAD-1).

        GL: windowed on the configured delete date field. AP: full reload by default
        (`apbill_full_reload`), else windowed on `apbill_delete_date_field`. Table/field
        names come entirely from config (validated); values are bound parameters.
        """
        gl_table = self._ident("gl_table")
        gl_field = safe_identifier(self.cfg.get("gl_delete_date_field", "Entry_Date"))
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
        if commit:
            self.conn.commit()

    def run_reconcile(self, start, end, *, commit: bool = True) -> None:
        """Post-load reconcile: sync AP links then update vendor info (specs/04 FR-LOAD-3).

        Order is fixed; proc names come from config (validated); dates are bound parameters.
        """
        sync = self._ident("proc_sync_links")
        vendor = self._ident("proc_update_vendor")
        with closing(self.conn.cursor()) as cur:
            cur.execute(f"EXEC {sync} ?, ?", (start, end))
            cur.execute(f"EXEC {vendor} ?, ?", (start, end))
        if commit:
            self.conn.commit()
