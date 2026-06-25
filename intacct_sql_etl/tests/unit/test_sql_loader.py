"""W1 unit tests — SQL loader with a fake DB connection (specs/04). Test-first.

Verifies: the configured test_ proc is called once per page-chunk, JSON passed as a BOUND
parameter (not string-interpolated), and commit() happens. Procs are black boxes.
"""
import json

import pytest

from intacct_etl import config, sql_loader


class FakeCursor:
    def __init__(self):
        self.calls = []
        self.closed = False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.cur = FakeCursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


TEST_SQL_CFG = {
    "gl_table": "test_GLTable",
    "apbill_table": "test_APBillTable",
    "proc_add_gl": "test_AddGLDetailBulk",
    "proc_add_ap": "test_AddAPBillBulk",
    "proc_sync_links": "test_SyncAPLinksToGL",
    "proc_update_vendor": "test_UpdateVendorInfoInGL",
}


def _records(n):
    return [{"RECORDNO": str(i), "AMOUNT": "1.00"} for i in range(n)]


def test_loader_rejects_prod_targets():
    bad = dict(TEST_SQL_CFG, proc_add_gl="AddGLDetailBulk")
    conn = FakeConnection()
    with pytest.raises(config.ConfigError):
        sql_loader.SqlLoader(conn, bad)


def test_load_gl_records_one_proc_call_per_chunk():
    conn = FakeConnection()
    loader = sql_loader.SqlLoader(conn, TEST_SQL_CFG)
    n = loader.load_gl_records(_records(5), page_size=2)
    assert n == 5
    # 5 records, page_size 2 -> 3 chunks -> 3 proc calls
    assert len(conn.cur.calls) == 3
    assert conn.commits == 1


def test_load_gl_uses_configured_proc_and_bound_param():
    conn = FakeConnection()
    loader = sql_loader.SqlLoader(conn, TEST_SQL_CFG)
    loader.load_gl_records(_records(2), page_size=10)
    sql, params = conn.cur.calls[0]
    assert "test_AddGLDetailBulk" in sql
    assert "?" in sql                       # parameter placeholder, not interpolation
    assert isinstance(params, (list, tuple)) and len(params) == 1
    # the bound param is the JSON page (so quotes are NOT escaped into the SQL text)
    assert json.loads(params[0]) == _records(2)
    assert "GLDETAIL" not in sql            # data isn't baked into the SQL string


def test_load_gl_empty_no_calls():
    conn = FakeConnection()
    loader = sql_loader.SqlLoader(conn, TEST_SQL_CFG)
    assert loader.load_gl_records([], page_size=2000) == 0
    assert conn.cur.calls == []


def test_load_closes_cursor():
    # CR-02: cursors must be closed deterministically (no leak)
    conn = FakeConnection()
    loader = sql_loader.SqlLoader(conn, TEST_SQL_CFG)
    loader.load_gl_records(_records(2), page_size=10)
    assert conn.cur.closed is True


def test_wipe_window_rejects_unsafe_identifier():
    # CR-19: identifiers are interpolated into SQL text -> validate them
    conn = FakeConnection()
    bad = dict(TEST_SQL_CFG, gl_table="test_GL; DROP TABLE x")
    loader = sql_loader.SqlLoader(conn, bad)
    with pytest.raises(config.ConfigError):
        loader.wipe_window("2026-01-01", "2026-01-31")
