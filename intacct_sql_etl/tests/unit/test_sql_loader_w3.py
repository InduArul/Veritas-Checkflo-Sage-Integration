"""W3 unit tests — wipe_window + run_reconcile + AP load (specs/04). All names from config.
Test-first."""
from datetime import date

from intacct_etl import sql_loader


class FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.cur = FakeCursor()
        self.commits = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


# Note custom (non-default) test_ names to prove NOTHING is hardcoded.
CFG = {
    "gl_table": "test_GLTable",
    "apbill_table": "test_APBillTable",
    "gl_delete_date_field": "Entry_Date",
    "apbill_full_reload": True,
    "proc_add_gl": "test_AddGLDetailBulk",
    "proc_add_ap": "test_AddAPBillBulk",
    "proc_sync_links": "test_SyncAPLinksToGL",
    "proc_update_vendor": "test_UpdateVendorInfoInGL",
}
S, E = date(2025, 1, 1), date(2025, 1, 31)


def _loader(cfg=CFG):
    return sql_loader.SqlLoader(FakeConnection(), cfg)


def test_wipe_window_gl_windowed_ap_full():
    ld = _loader()
    ld.wipe_window(S, E)
    calls = ld.conn.cur.calls
    gl = next(c for c in calls if "test_GLTable" in c[0])
    ap = next(c for c in calls if "test_APBillTable" in c[0])
    assert "DELETE FROM test_GLTable" in gl[0]
    assert "Entry_Date" in gl[0] and "?" in gl[0]      # configured field, bound params
    assert gl[1] == (S, E)
    assert "DELETE FROM test_APBillTable" in ap[0]
    assert "WHERE" not in ap[0].upper()                # full reload -> no filter
    assert ld.conn.commits == 1


def test_wipe_window_ap_windowed_when_not_full_reload():
    cfg = dict(CFG, apbill_full_reload=False, apbill_delete_date_field="WHENPOSTED")
    ld = _loader(cfg)
    ld.wipe_window(S, E)
    ap = next(c for c in ld.conn.cur.calls if "test_APBillTable" in c[0])
    assert "WHENPOSTED" in ap[0] and "?" in ap[0]
    assert ap[1] == (S, E)


def test_run_reconcile_calls_both_procs_in_order_with_dates():
    ld = _loader()
    ld.run_reconcile(S, E)
    calls = [c for c in ld.conn.cur.calls if "EXEC" in c[0]]
    assert "test_SyncAPLinksToGL" in calls[0][0]
    assert "test_UpdateVendorInfoInGL" in calls[1][0]
    assert calls[0][1] == (S, E) and calls[1][1] == (S, E)


def test_reconcile_uses_configured_proc_names():
    cfg = dict(CFG, proc_sync_links="test_MySync", proc_update_vendor="test_MyVendor")
    ld = _loader(cfg)
    ld.run_reconcile(S, E)
    execs = [c[0] for c in ld.conn.cur.calls if "EXEC" in c[0]]
    assert any("test_MySync" in s for s in execs)
    assert any("test_MyVendor" in s for s in execs)


def test_load_ap_records_uses_configured_proc():
    ld = _loader()
    n = ld.load_ap_records([{"RECORDNO": "1"}, {"RECORDNO": "2"}], page_size=10)
    assert n == 2
    assert any("test_AddAPBillBulk" in c[0] for c in ld.conn.cur.calls)
