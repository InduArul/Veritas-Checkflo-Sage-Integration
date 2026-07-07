"""SQL Server load via injected DB connection (specs/04). Procs are black boxes.

The connection is any DB-API-like object exposing cursor()/commit()/rollback(); the cursor
exposes execute(sql, params) and close(). Real wiring uses pyodbc (kept out of import path so
unit tests need no driver). All targets MUST be test_-prefixed (defense-in-depth vs
prod_write_guard) and every interpolated identifier is validated (CR-19).
"""
from __future__ import annotations

import time
from contextlib import closing
from dataclasses import dataclass, field
from typing import Callable

from . import transform
from .config import assert_test_sql_targets, safe_identifier
from .pagination import DEFAULT_PAGE_SIZE


class SweepGuardError(Exception):
    """Raised when a delete-sweep would remove more than the configured fraction of scanned rows."""


@dataclass
class LoaderMetrics:
    """Per-loader (per-environment) load metrics for the audit trail (specs/12)."""
    gl_pages: int = 0
    ap_pages: int = 0
    gl_rows_affected: int = 0
    ap_rows_affected: int = 0
    gl_rows_affected_known: bool = True   # W8/OQ-15: False once a page's proc reports no rowcount (-1)
    ap_rows_affected_known: bool = True
    gl_encode_s: float = 0.0
    ap_encode_s: float = 0.0
    gl_db_s: float = 0.0
    ap_db_s: float = 0.0
    wipe_s: float = 0.0
    reconcile_s: float = 0.0
    gl_deleted: int = 0                  # specs/13 delete-sweep counts
    ap_deleted: int = 0
    gl_deleted_keys: list = field(default_factory=list)   # W8: WHICH keys were swept (auditability)
    ap_deleted_keys: list = field(default_factory=list)
    sweep_s: float = 0.0

    def record_page(self, kind: str, *, encode_s: float, db_s: float, affected: int) -> None:
        setattr(self, f"{kind}_pages", getattr(self, f"{kind}_pages") + 1)
        setattr(self, f"{kind}_encode_s", getattr(self, f"{kind}_encode_s") + encode_s)
        setattr(self, f"{kind}_db_s", getattr(self, f"{kind}_db_s") + db_s)
        if affected < 0:
            # The proc reported no rowcount (SET NOCOUNT / OPENJSON merge). Don't fake a 0 — flag it
            # unknown so a genuinely-zero-change run stays distinguishable (W8 Item B / OQ-15).
            setattr(self, f"{kind}_rows_affected_known", False)
        else:
            setattr(self, f"{kind}_rows_affected",
                    getattr(self, f"{kind}_rows_affected") + affected)


class SqlLoader:
    def __init__(self, connection, sql_cfg: dict, *, source_env: str | None = None,
                 require_test_targets: bool = True,
                 clock: Callable[[], float] = time.monotonic):
        self.conn = connection
        self.cfg = sql_cfg
        self.source_env = source_env      # multi-entity tag (specs/04 FR-LOAD-7, OQ-31)
        self.clock = clock
        self.metrics = LoaderMetrics()
        if require_test_targets:
            # CR-09: single shared guard (no duplicated copy here)
            assert_test_sql_targets(sql_cfg)

    def _ident(self, key: str) -> str:
        """Fetch a configured table/proc/field name and validate it as a safe identifier."""
        return safe_identifier(str(self.cfg[key]))

    def _source_env_col(self) -> str:
        """Validated name of the entity discriminator column (default Source_Env)."""
        return safe_identifier(str(self.cfg.get("source_env_column", "Source_Env")))

    def _exec_proc_json(self, proc_name: str, json_str: str) -> int:
        """Execute the bulk proc with the page + @Source_Env bound as parameters. Returns the RAW
        cursor.rowcount for the audit trail: a value <0 means the proc reported no count and is
        preserved (NOT clamped to 0) so `record_page` can flag the metric unknown (W8 Item B/OQ-15).

        The page is single-env, so @Source_Env stamps the whole batch (specs/04 FR-LOAD-7)."""
        proc = safe_identifier(proc_name)
        with closing(self.conn.cursor()) as cur:
            # Bound parameters — JSON is NOT interpolated into the SQL text (no quote escaping).
            cur.execute(f"EXEC {proc} ?, ?", (json_str, self.source_env))
            rc = getattr(cur, "rowcount", 0)
            return int(rc) if rc is not None else -1

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

        GL: windowed on the configured delete date field. AP: "full reload" by default
        (`apbill_full_reload`), else windowed on `apbill_delete_date_field`. Every delete is
        scoped by `Source_Env` (specs/04 FR-LOAD-7) so one env's refresh never touches another's
        rows — "full reload" now means "all of THIS env's AP rows", not the whole table. Table/
        field names come entirely from config (validated); values are bound parameters.
        """
        gl_table = self._ident("gl_table")
        gl_field = safe_identifier(self.cfg.get("gl_delete_date_field", "Entry_Date"))
        se = self._source_env_col()
        t0 = self.clock()
        with closing(self.conn.cursor()) as cur:
            cur.execute(
                f"DELETE FROM {gl_table} WHERE {gl_field} >= ? AND {gl_field} <= ? AND {se} = ?",
                (start, end, self.source_env),
            )
            ap_table = self._ident("apbill_table")
            if self.cfg.get("apbill_full_reload", True):
                cur.execute(f"DELETE FROM {ap_table} WHERE {se} = ?", (self.source_env,))
            else:
                ap_field = safe_identifier(self.cfg.get("apbill_delete_date_field", "WHENPOSTED"))
                cur.execute(
                    f"DELETE FROM {ap_table} WHERE {ap_field} >= ? AND {ap_field} <= ? AND {se} = ?",
                    (start, end, self.source_env),
                )
        self.metrics.wipe_s += self.clock() - t0
        if commit:
            self.conn.commit()

    # -- delta delete-sweep (specs/13 §13.5): delete SQL rows absent from Intacct's key set --

    @staticmethod
    def _guard_mass_delete(kind: str, missing: int, scanned: int, fraction) -> None:
        """Abort a sweep that would delete more than `fraction` of the scanned rows (specs/13 §13.5).

        Defense-in-depth against a mis-scoped/partial key set silently wiping a window. Raises rather
        than deleting; the caller's transaction rolls back and the run is flagged/alerted.
        """
        if fraction is None or scanned <= 0:
            return
        if missing > fraction * scanned:
            raise SweepGuardError(
                f"{kind} sweep would delete {missing}/{scanned} rows "
                f"(> {fraction:.0%} guard); aborting — investigate the Intacct key pull"
            )

    def sweep_gl(self, present_keys, start, end, *, commit: bool = True,
                 max_delete_fraction=None) -> int:
        """Delete GL rows in the Entry_Date window whose natural key is NOT in `present_keys`.

        `present_keys` = set of key tuples (as strings) currently in Intacct. Reads the SQL keys in
        the window, deletes only those missing from Intacct (never inserts/updates). Returns count.
        """
        gl = self._ident("gl_table")
        field = safe_identifier(self.cfg.get("gl_delete_date_field", "Entry_Date"))
        cols = [safe_identifier(c) for c in self.cfg.get("gl_key_columns", ["Record_No", "Line_No"])]
        se = self._source_env_col()
        collist = ", ".join(cols)
        where = " AND ".join(f"{c} = ?" for c in cols)
        t0 = self.clock()
        with closing(self.conn.cursor()) as cur:
            # Entity-scoped: only THIS env's rows are read/compared/deleted (specs/04 FR-LOAD-7).
            cur.execute(f"SELECT {collist} FROM {gl} WHERE {field} >= ? AND {field} <= ? AND {se} = ?",
                        (start, end, self.source_env))
            rows = cur.fetchall()
            missing = [tuple(r) for r in rows if tuple(str(x) for x in r) not in present_keys]
            self._guard_mass_delete("GL", len(missing), len(rows), max_delete_fraction)
            for key in missing:
                cur.execute(f"DELETE FROM {gl} WHERE {where} AND {se} = ?", tuple(key) + (self.source_env,))
        self.metrics.gl_deleted += len(missing)
        self.metrics.gl_deleted_keys.extend([str(x) for x in key] for key in missing)  # W8: JSON-safe keys
        self.metrics.sweep_s += self.clock() - t0
        if commit:
            self.conn.commit()
        return len(missing)

    def sweep_ap(self, present_keys, start, end, *, date_field, commit: bool = True,
                 max_delete_fraction=None) -> int:
        """Delete AP rows in the `date_field` window whose key is NOT in `present_keys`.

        AP is windowed exactly like GL (on a real date column) so history outside the window is never
        touched — this respects the accumulate rule (specs/13 §13.9). The caller must only invoke this
        when a date column exists (`apbill_sweep_date_field`); there is no destructive full-table mode.
        """
        ap = self._ident("apbill_table")
        col = safe_identifier(self.cfg.get("ap_key_column", "Record_No"))
        dfield = safe_identifier(date_field)
        se = self._source_env_col()
        t0 = self.clock()
        with closing(self.conn.cursor()) as cur:
            # Entity-scoped: only THIS env's rows are read/compared/deleted (specs/04 FR-LOAD-7).
            cur.execute(f"SELECT {col} FROM {ap} WHERE {dfield} >= ? AND {dfield} <= ? AND {se} = ?",
                        (start, end, self.source_env))
            rows = cur.fetchall()
            missing = [tuple(r) for r in rows if tuple(str(x) for x in r) not in present_keys]
            self._guard_mass_delete("AP", len(missing), len(rows), max_delete_fraction)
            for key in missing:
                cur.execute(f"DELETE FROM {ap} WHERE {col} = ? AND {se} = ?", tuple(key) + (self.source_env,))
        self.metrics.ap_deleted += len(missing)
        self.metrics.ap_deleted_keys.extend([str(x) for x in key] for key in missing)  # W8: JSON-safe keys
        self.metrics.sweep_s += self.clock() - t0
        if commit:
            self.conn.commit()
        return len(missing)

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
