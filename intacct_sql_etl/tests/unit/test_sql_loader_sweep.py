"""W-delta unit tests — loader delete-sweep (specs/13 §13.5). Test-first.

sweep_gl: read SQL GL keys in the Entry_Date window, delete rows whose key is absent from the
Intacct-present set. sweep_ap: full-key compare (APBillTable has no date column). All identifiers
from config + validated; keys bound as params; deletes only (never inserts/updates).
"""
from datetime import date

import pytest

from intacct_etl import sql_loader


class FakeCursor:
    def __init__(self, rows):
        self.calls = []
        self._rows = rows

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConnection:
    def __init__(self, rows):
        self.cur = FakeCursor(rows)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


CFG = {
    "gl_table": "test_GLTable",
    "apbill_table": "test_APBillTable",
    "watermark_table": "test_etl_watermark",
    "gl_delete_date_field": "Entry_Date",
    "gl_key_columns": ["Record_No", "Line_No"],
    "ap_key_column": "Record_No",
    "proc_add_gl": "test_AddGLDetailBulk",
    "proc_add_ap": "test_AddAPBillBulk",
    "proc_sync_links": "test_SyncAPLinksToGL",
    "proc_update_vendor": "test_UpdateVendorInfoInGL",
}
S, E = date(2026, 3, 1), date(2026, 6, 30)
ENV = "Evergreen"


def _loader(rows):
    return sql_loader.SqlLoader(FakeConnection(rows), CFG, source_env=ENV)


def test_sweep_gl_deletes_only_absent_keys():
    sql_rows = [("100", "1"), ("101", "1"), ("102", "2")]     # SQL has 3 GL rows
    ld = _loader(sql_rows)
    present = {("100", "1"), ("102", "2")}                     # 101/1 no longer in Intacct
    n = ld.sweep_gl(present, S, E)
    assert n == 1
    reads = [c for c in ld.conn.cur.calls if "SELECT" in c[0].upper()]
    assert "Entry_Date" in reads[0][0] and "Source_Env" in reads[0][0] and reads[0][1] == (S, E, ENV)
    dels = [c for c in ld.conn.cur.calls if "DELETE" in c[0].upper()]
    assert len(dels) == 1 and dels[0][1] == ("101", "1", ENV) and "Source_Env" in dels[0][0]
    assert ld.conn.commits == 1
    assert ld.metrics.gl_deleted == 1


def test_sweep_gl_no_delete_when_all_present():
    ld = _loader([("100", "1")])
    n = ld.sweep_gl({("100", "1")}, S, E)
    assert n == 0
    assert not [c for c in ld.conn.cur.calls if "DELETE" in c[0].upper()]


def test_sweep_ap_windowed_on_date_field():
    # AP sweep is windowed on a real date column (respects accumulate — old rows never touched).
    ld = _loader([("200",), ("201",)])
    n = ld.sweep_ap({("200",)}, S, E, date_field="When_Posted")   # 201 no longer in Intacct
    assert n == 1
    read = [c for c in ld.conn.cur.calls if "SELECT" in c[0].upper()][0]
    assert "When_Posted" in read[0] and read[1] == (S, E, ENV)   # windowed + entity-scoped
    dels = [c for c in ld.conn.cur.calls if "DELETE" in c[0].upper()]
    assert dels[0][1] == ("201", ENV)
    assert ld.metrics.ap_deleted == 1


def test_sweep_gl_aborts_on_mass_delete():
    # Mass-delete guard: refuse to delete more than the configured fraction of scanned rows.
    ld = _loader([("1", "1"), ("2", "1"), ("3", "1"), ("4", "1")])
    with pytest.raises(sql_loader.SweepGuardError):
        ld.sweep_gl(set(), S, E, max_delete_fraction=0.5)      # would delete 4/4 (100% > 50%)
    assert not [c for c in ld.conn.cur.calls if "DELETE" in c[0].upper()]   # nothing deleted
    assert ld.conn.commits == 0


def test_sweep_gl_allows_delete_within_threshold():
    ld = _loader([("1", "1"), ("2", "1"), ("3", "1"), ("4", "1")])
    n = ld.sweep_gl({("1", "1"), ("2", "1"), ("3", "1")}, S, E, max_delete_fraction=0.5)  # 1/4
    assert n == 1


def test_sweep_uses_configured_identifiers():
    cfg = dict(CFG, gl_table="test_MyGL", gl_key_columns=["RecNo", "Ln"])
    ld = sql_loader.SqlLoader(FakeConnection([("1", "1")]), cfg, source_env=ENV)
    ld.sweep_gl({("1", "1")}, S, E)
    assert any("test_MyGL" in c[0] and "RecNo" in c[0] for c in ld.conn.cur.calls)


# ---- regression: a windowed sweep must NEVER read/delete out-of-window history (2027 scenario) ----

class _DatedCursor:
    """Models the SQL side honestly: a windowed SELECT returns ONLY rows whose date is in [start,end];
    DELETE removes by key. Proves out-of-window rows are never even read, so never deleted."""

    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0
        self._result = []

    def execute(self, sql, params=None):
        self.conn.calls.append((sql, params))
        flat = sql.upper().replace(" ", "")
        if flat.startswith("SELECT"):
            start, end = params[0], params[1]      # params now = (start, end, source_env)
            if "TEST_GLTABLE" in flat:
                self._result = [k for (k, dt) in self.conn.gl if start <= dt <= end]
            else:
                self._result = [k for (k, dt) in self.conn.ap if start <= dt <= end]
        elif flat.startswith("DELETE"):
            if "TEST_GLTABLE" in flat:
                key = (params[0], params[1])
                self.conn.gl = [(k, dt) for (k, dt) in self.conn.gl if k != key]
            else:
                key = (params[0],)
                self.conn.ap = [(k, dt) for (k, dt) in self.conn.ap if k != key]

    def fetchall(self):
        return self._result

    def close(self):
        pass


class _DatedConn:
    def __init__(self, gl=None, ap=None):
        self.gl = list(gl or [])            # [((Record_No, Line_No), date), ...]
        self.ap = list(ap or [])            # [((Record_No,), date), ...]
        self.calls = []
        self.commits = 0

    def cursor(self):
        return _DatedCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


W_START, W_END = date(2026, 1, 1), date(2027, 7, 31)      # a 2027 monthly-style window


def test_gl_sweep_2027_never_touches_out_of_window_history():
    conn = _DatedConn(gl=[
        (("2024A", "1"), date(2024, 3, 1)),               # out of window (must survive)
        (("2026A", "1"), date(2026, 5, 1)),               # in window, still in Intacct
        (("2027GONE", "1"), date(2027, 2, 1)),            # in window, deleted in Intacct
    ])
    ld = sql_loader.SqlLoader(conn, CFG, source_env=ENV)
    n = ld.sweep_gl({("2026A", "1")}, W_START, W_END)     # Intacct in-window set
    survivors = {k for (k, _) in conn.gl}
    assert ("2024A", "1") in survivors                    # 2024 history untouched
    assert ("2026A", "1") in survivors                    # still-existing kept
    assert ("2027GONE", "1") not in survivors             # genuine deletion removed
    assert n == 1


# ---- W8 observability: capture WHICH keys were deleted, not just the count ----

def test_sweep_gl_records_deleted_keys():
    # W8 Item A1: the deleted GL keys must be captured (auditability), not just len().
    ld = _loader([("100", "1"), ("101", "1"), ("102", "2")])
    ld.sweep_gl({("100", "1"), ("102", "2")}, S, E)          # 101/1 is the deletion
    assert ld.metrics.gl_deleted == 1
    assert ld.metrics.gl_deleted_keys == [["101", "1"]]      # JSON-safe list of key parts


def test_sweep_ap_records_deleted_keys():
    ld = _loader([("200",), ("201",)])
    ld.sweep_ap({("200",)}, S, E, date_field="When_Posted")  # 201 is the deletion
    assert ld.metrics.ap_deleted == 1
    assert ld.metrics.ap_deleted_keys == [["201"]]


def test_sweep_no_delete_captures_no_keys():
    ld = _loader([("100", "1")])
    ld.sweep_gl({("100", "1")}, S, E)
    assert ld.metrics.gl_deleted_keys == []                  # empty, not None


def test_ap_sweep_2027_never_touches_out_of_window_history():
    conn = _DatedConn(ap=[
        (("2024B",), date(2024, 9, 1)),                   # out of window (must survive)
        (("2026B",), date(2026, 4, 1)),                   # in window, still in Intacct
        (("2027GONE",), date(2027, 3, 1)),                # in window, deleted in Intacct
    ])
    ld = sql_loader.SqlLoader(conn, CFG, source_env=ENV)
    n = ld.sweep_ap({("2026B",)}, W_START, W_END, date_field="When_Posted")
    survivors = {k for (k, _) in conn.ap}
    assert ("2024B",) in survivors                        # 2024 history untouched
    assert ("2026B",) in survivors
    assert ("2027GONE",) not in survivors
    assert n == 1
