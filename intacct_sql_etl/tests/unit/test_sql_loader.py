"""W1 unit tests — SQL loader with a fake DB connection (specs/04). Test-first.

Verifies: the configured test_ proc is called once per page-chunk, JSON passed as a BOUND
parameter (not string-interpolated), and commit() happens. Procs are black boxes.
"""
import json

import pytest

from intacct_etl import config, sql_loader


class FakeCursor:
    def __init__(self, rowcount=7, counts=(5, 2)):
        self.calls = []
        self.closed = False
        self.rowcount = rowcount
        self._counts = counts            # OQ-15: proc now returns a (inserted, updated) result set

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self._counts

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
    assert "?, ?" in sql                    # bound params: json page + @Source_Env
    assert isinstance(params, (list, tuple)) and len(params) == 2
    # the first bound param is the JSON page (so quotes are NOT escaped into the SQL text)
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


def _counter():
    box = {"n": 0}

    def clock():
        box["n"] += 1
        return box["n"]

    return clock


def test_loader_captures_metrics():
    # specs/12 FR-AUD-3/4 + OQ-15: pages, inserted/updated split, encode + db timings per object
    conn = FakeConnection()
    loader = sql_loader.SqlLoader(conn, TEST_SQL_CFG, clock=_counter())
    loader.load_gl_records(_records(5), page_size=2)      # 5 rows / 2 -> 3 pages, each proc -> (5,2)
    m = loader.metrics
    assert m.gl_pages == 3
    assert m.gl_inserted == 3 * 5 and m.gl_updated == 3 * 2       # OQ-15 authoritative split
    assert m.gl_rows_affected == 3 * 7                           # inserted + updated
    assert m.gl_rows_affected_known is True
    assert m.gl_encode_s == 3.0 and m.gl_db_s == 3.0      # 1.0s each per page (fake clock)


# ---- rows_affected honesty + OQ-15 split: metric comes from the proc's (inserted,updated) result set ----

class _CountsCursor:
    def __init__(self, counts):
        self.calls = []
        self.closed = False
        self.rowcount = -1
        self._counts = counts            # a (inserted, updated) tuple, or None to simulate "no result set"

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        if self._counts is None:
            raise Exception("No results.  Previous SQL was not a query.")   # pyodbc when no result set
        return self._counts

    def close(self):
        self.closed = True


class _CountsConn:
    def __init__(self, counts):
        self.cur = _CountsCursor(counts)
        self.commits = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def test_rows_affected_unknown_when_proc_returns_no_counts():
    # Old/un-migrated proc returns no result set. The metric must be flagged UNKNOWN, not a silent 0
    # (a genuinely-zero-change run stays distinguishable from an uncaptured one).
    loader = sql_loader.SqlLoader(_CountsConn(None), TEST_SQL_CFG)
    loader.load_gl_records(_records(2), page_size=10)
    assert loader.metrics.gl_rows_affected_known is False
    assert loader.metrics.gl_rows_affected == 0
    assert loader.metrics.gl_inserted == 0 and loader.metrics.gl_updated == 0


def test_split_counts_from_proc_result_set():
    # OQ-15: the MERGE proc OUTPUTs (inserted, updated) -> authoritative split.
    loader = sql_loader.SqlLoader(_CountsConn((3, 1)), TEST_SQL_CFG)
    loader.load_gl_records(_records(2), page_size=10)         # 1 page -> (3 inserted, 1 updated)
    assert loader.metrics.gl_inserted == 3 and loader.metrics.gl_updated == 1
    assert loader.metrics.gl_rows_affected == 4               # inserted + updated
    assert loader.metrics.gl_rows_affected_known is True


def test_wipe_and_reconcile_timed():
    conn = FakeConnection()
    loader = sql_loader.SqlLoader(conn, TEST_SQL_CFG, clock=_counter())
    loader.wipe_window("2026-01-01", "2026-01-31")
    loader.run_reconcile("2026-01-01", "2026-01-31")
    assert loader.metrics.wipe_s > 0
    assert loader.metrics.reconcile_s > 0


def test_wipe_window_rejects_unsafe_identifier():
    # CR-19: identifiers are interpolated into SQL text -> validate them
    conn = FakeConnection()
    bad = dict(TEST_SQL_CFG, gl_table="test_GL; DROP TABLE x")
    loader = sql_loader.SqlLoader(conn, bad)
    with pytest.raises(config.ConfigError):
        loader.wipe_window("2026-01-01", "2026-01-31")
