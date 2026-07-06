"""Multi-entity Source_Env plumbing (OQ-31, specs/04 FR-LOAD-7). Test-first.

The loader must (a) pass @Source_Env to the bulk procs and (b) scope every delete
(wipe_window, sweep_gl, sweep_ap) by Source_Env so one env's run never touches another's rows.
"""
from datetime import date

import pytest

from intacct_etl import sql_loader


class FakeCursor:
    def __init__(self, rows=None):
        self.calls = []
        self._rows = rows or []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConnection:
    def __init__(self, rows=None):
        self.cur = FakeCursor(rows)
        self.commits = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


CFG = {
    "gl_table": "test_GLTable",
    "apbill_table": "test_APBillTable",
    "gl_delete_date_field": "Entry_Date",
    "apbill_full_reload": True,
    "gl_key_columns": ["Record_No", "Line_No"],
    "ap_key_column": "Record_No",
    "proc_add_gl": "test_AddGLDetailBulk",
    "proc_add_ap": "test_AddAPBillBulk",
    "proc_sync_links": "test_SyncAPLinksToGL",
    "proc_update_vendor": "test_UpdateVendorInfoInGL",
}
S, E = date(2025, 1, 1), date(2025, 1, 31)
ENV = "Evergreen"


def _loader(conn=None, cfg=CFG):
    return sql_loader.SqlLoader(conn or FakeConnection(), cfg, source_env=ENV)


def test_load_gl_passes_source_env_param():
    ld = _loader()
    ld.load_gl_records([{"RECORDNO": "1", "LINE_NO": "1"}], page_size=10)
    ex = next(c for c in ld.conn.cur.calls if "EXEC" in c[0] and "test_AddGLDetailBulk" in c[0])
    assert "?, ?" in ex[0]                       # json + @Source_Env
    assert ex[1][1] == ENV


def test_load_ap_passes_source_env_param():
    ld = _loader()
    ld.load_ap_records([{"RECORDNO": "1"}], page_size=10)
    ex = next(c for c in ld.conn.cur.calls if "EXEC" in c[0] and "test_AddAPBillBulk" in c[0])
    assert "?, ?" in ex[0]
    assert ex[1][1] == ENV


def test_wipe_window_scopes_gl_and_ap_by_source_env():
    ld = _loader()
    ld.wipe_window(S, E)
    gl = next(c for c in ld.conn.cur.calls if "test_GLTable" in c[0])
    ap = next(c for c in ld.conn.cur.calls if "test_APBillTable" in c[0])
    assert "Source_Env" in gl[0] and gl[1] == (S, E, ENV)
    # AP full reload is NO LONGER a whole-table wipe — it is scoped to this env.
    assert "Source_Env" in ap[0] and ap[1] == (ENV,)


def test_sweep_gl_scopes_read_and_delete_by_source_env():
    conn = FakeConnection(rows=[("9", "1")])          # one SQL key in window
    ld = _loader(conn)
    ld.sweep_gl(present_keys=set(), start=S, end=E)    # nothing present -> the row is "missing"
    sel = next(c for c in conn.cur.calls if c[0].strip().upper().startswith("SELECT"))
    dele = next(c for c in conn.cur.calls if c[0].strip().upper().startswith("DELETE"))
    assert "Source_Env" in sel[0] and ENV in sel[1]
    assert "Source_Env" in dele[0] and ENV in dele[1]


def test_sweep_ap_scopes_read_and_delete_by_source_env():
    conn = FakeConnection(rows=[("100",)])
    ld = _loader(conn)
    ld.sweep_ap(present_keys=set(), start=S, end=E, date_field="When_Posted")
    sel = next(c for c in conn.cur.calls if c[0].strip().upper().startswith("SELECT"))
    dele = next(c for c in conn.cur.calls if c[0].strip().upper().startswith("DELETE"))
    assert "Source_Env" in sel[0] and ENV in sel[1]
    assert "Source_Env" in dele[0] and ENV in dele[1]
