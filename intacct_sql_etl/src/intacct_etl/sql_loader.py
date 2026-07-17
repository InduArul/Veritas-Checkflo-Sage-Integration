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
from .config import ConfigError, assert_test_sql_targets, safe_identifier
from .pagination import DEFAULT_PAGE_SIZE


class SweepGuardError(Exception):
    """Raised when a delete-sweep would remove more than the configured fraction of scanned rows."""


def _split_object_name(qualified: str) -> "tuple[str | None, str]":
    """Split a (possibly schema-qualified/bracketed) table name into (schema_or_None, bare_name).

    INFORMATION_SCHEMA.COLUMNS stores TABLE_SCHEMA and TABLE_NAME in SEPARATE columns, so a lookup by
    the qualified string (`dbo.GLTable`) matches nothing. 'dbo.GLTable' -> ('dbo','GLTable');
    '[dbo].[GLTable]' -> ('dbo','GLTable'); 'GLTable' -> (None,'GLTable')."""
    parts = str(qualified).replace("[", "").replace("]", "").split(".")
    bare = parts[-1].strip()
    schema = parts[-2].strip() if len(parts) >= 2 and parts[-2].strip() else None
    return schema, bare


@dataclass
class LoaderMetrics:
    """Per-loader (per-environment) load metrics for the audit trail (specs/12)."""
    gl_pages: int = 0
    ap_pages: int = 0
    gl_rows_affected: int = 0
    ap_rows_affected: int = 0
    gl_inserted: int = 0                  # OQ-15: authoritative split from the MERGE $action
    ap_inserted: int = 0
    gl_updated: int = 0
    ap_updated: int = 0
    gl_rows_affected_known: bool = True   # W8/OQ-15: False if a page's proc returned no counts result set
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

    def record_page(self, kind: str, *, encode_s: float, db_s: float,
                    result: "tuple[int, int] | None") -> None:
        setattr(self, f"{kind}_pages", getattr(self, f"{kind}_pages") + 1)
        setattr(self, f"{kind}_encode_s", getattr(self, f"{kind}_encode_s") + encode_s)
        setattr(self, f"{kind}_db_s", getattr(self, f"{kind}_db_s") + db_s)
        if result is None:
            # The proc returned no (inserted, updated) result set (old/un-migrated proc). Don't fake a
            # 0 — flag it unknown so a genuinely-zero-change run stays distinguishable (W8 B / OQ-15).
            setattr(self, f"{kind}_rows_affected_known", False)
        else:
            ins, upd = result
            setattr(self, f"{kind}_inserted", getattr(self, f"{kind}_inserted") + ins)
            setattr(self, f"{kind}_updated", getattr(self, f"{kind}_updated") + upd)
            setattr(self, f"{kind}_rows_affected",
                    getattr(self, f"{kind}_rows_affected") + ins + upd)


class SqlLoader:
    def __init__(self, connection, sql_cfg: dict, *, source_env: str | None = None,
                 require_test_targets: bool = True, run_id: str | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.conn = connection
        self.cfg = sql_cfg
        self.source_env = source_env      # multi-entity tag (specs/04 FR-LOAD-7, OQ-31)
        self.run_id = run_id              # W8 A3: stamped on archived deleted rows
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

    def _archive_table(self, cfg_key: str) -> "str | None":
        """Validated archive-table name when delete-archiving is enabled (W8 A3), else None."""
        if not self.cfg.get("archive_deletes"):
            return None
        name = self.cfg.get(cfg_key)
        return safe_identifier(str(name)) if name else None

    def _ordered_columns(self, cur, table: str) -> "list[str]":
        """Physical column names of `table`, in ordinal order (cached per run). Used to build a
        column-EXPLICIT archive insert so source→archive alignment is by NAME, not position."""
        cache = self.__dict__.setdefault("_col_cache", {})
        if table not in cache:
            schema, bare = _split_object_name(table)
            if schema:
                cur.execute(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_NAME = ? AND TABLE_SCHEMA = ? ORDER BY ORDINAL_POSITION",
                    (bare, schema),
                )
            else:
                cur.execute(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
                    (bare,),
                )
            cols = [safe_identifier(r[0]) for r in cur.fetchall()]
            if not cols:                                 # never build `INSERT (, run_id, ...)`
                raise ConfigError(
                    f"archive source table {table!r} resolved to ZERO columns "
                    f"(bare={bare!r}, schema={schema!r}); refusing to build an empty archive insert"
                )
            cache[table] = cols
        return cache[table]

    def _archive_row(self, cur, arch: "str | None", src_table: str, where: str, key: tuple) -> None:
        """Snapshot a soon-to-be-deleted row into the archive (full row + run_id + deleted_at) BEFORE
        the DELETE, so every swept row is recoverable/inspectable. Entity-scoped. No-op if disabled.

        Columns are named EXPLICITLY (aligned by name), never `SELECT *`: a positional insert silently
        breaks if the archive table's column order differs from the source's — the root cause of the
        2026-07-16 AP_deleted_archive 22007 (Source_Env string landing in the When_Posted DATE column).
        The archive must expose every source column by the same name, then run_id, deleted_at."""
        if not arch:
            return
        se = self._source_env_col()
        cols = self._ordered_columns(cur, src_table)
        collist = ", ".join(cols)
        cur.execute(
            f"INSERT INTO {arch} ({collist}, run_id, deleted_at) "
            f"SELECT {collist}, ?, SYSUTCDATETIME() FROM {src_table} "
            f"WHERE {where} AND {se} = ?",
            (self.run_id,) + key + (self.source_env,),
        )

    def _exec_proc_json(self, proc_name: str, json_str: str):
        """Execute the bulk proc with the page + @Source_Env bound as parameters. The MERGE proc
        OUTPUTs a one-row `(inserted, updated)` result set (OQ-15); returns that tuple. Returns None
        when no result set is available (old/un-migrated proc) so `record_page` flags the metric
        unknown rather than faking a 0 (W8 Item B).

        The page is single-env, so @Source_Env stamps the whole batch (specs/04 FR-LOAD-7)."""
        proc = safe_identifier(proc_name)
        with closing(self.conn.cursor()) as cur:
            # Bound parameters — JSON is NOT interpolated into the SQL text (no quote escaping).
            cur.execute(f"EXEC {proc} ?, ?", (json_str, self.source_env))
            row = self._first_row(cur)
            if row is None or len(row) < 2:
                return None
            return (int(row[0] or 0), int(row[1] or 0))

    @staticmethod
    def _first_row(cur):
        """Return the first result-set row, skipping any leading no-result statements (DML before the
        SELECT). Returns None if the proc produced no fetchable row. Never raises."""
        while True:
            try:
                return cur.fetchone()
            except Exception:
                pass
            nextset = getattr(cur, "nextset", None)
            try:
                if not nextset or not nextset():
                    return None
            except Exception:
                return None

    def _load_records(self, proc_key: str, records: list[dict], page_size: int,
                      commit: bool, kind: str) -> int:
        proc = self.cfg[proc_key]
        loaded = 0
        for i in range(0, len(records), page_size):
            chunk = records[i:i + page_size]
            t0 = self.clock()
            payload = transform.encode_page(chunk)          # Transform step (specs/12)
            t1 = self.clock()
            result = self._exec_proc_json(proc, payload)    # Load step (specs/12) -> (inserted, updated)
            t2 = self.clock()
            self.metrics.record_page(kind, encode_s=t1 - t0, db_s=t2 - t1, result=result)
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
            arch = self._archive_table("gl_deleted_archive_table")
            for key in missing:
                self._archive_row(cur, arch, gl, where, tuple(key))    # W8 A3: snapshot before delete
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
            arch = self._archive_table("ap_deleted_archive_table")
            for key in missing:
                self._archive_row(cur, arch, ap, f"{col} = ?", tuple(key))    # W8 A3: snapshot before delete
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
