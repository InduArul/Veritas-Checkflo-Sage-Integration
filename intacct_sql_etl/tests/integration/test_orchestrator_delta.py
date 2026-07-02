"""W-delta integration — orchestrator delta modes (specs/13 §13.4/§13.7). Test-first.

Modes: upsert (delta only), sweep (delete-sweep only), sweep-upsert (both). No bulk wipe.
Watermark advances only on a clean object fetch (completeness gate, §13.11).
"""
from datetime import date, datetime

import pytest

from intacct_etl import orchestrator

pytestmark = pytest.mark.integration

WM = "test_etl_watermark"


def base_cfg(mode, **over):
    cfg = {
        "run": {"test_mode": True, "daily_lookback_months": 3, "mode": mode},
        "environments": [{"name": "Evergreen", "sage_creds_key": "K1", "sender_id": "s",
                          "sender_password": "p", "user_id": "u", "user_password": "p",
                          "company_id": "c"}],
        "sage": {"url": "https://sage", "page_size": 2000, "watermark_grace_hours": 1},
        "gl": {}, "ap": {},
        "sql": {"gl_table": "test_GLTable", "apbill_table": "test_APBillTable",
                "watermark_table": WM, "gl_delete_date_field": "Entry_Date",
                "gl_key_columns": ["Record_No", "Line_No"], "ap_key_column": "Record_No",
                "proc_add_gl": "test_AddGLDetailBulk", "proc_add_ap": "test_AddAPBillBulk",
                "proc_sync_links": "test_SyncAPLinksToGL",
                "proc_update_vendor": "test_UpdateVendorInfoInGL", "load_strategy": "transaction"},
        "retry": {}, "log": {"sink": "stdout"},
    }
    cfg["run"].update(over.get("run", {}))
    return cfg


class FakeCursor:
    def __init__(self, sink, wm_row, gl_rows, ap_rows):
        self.sink = sink
        self.wm_row = wm_row
        self.gl_rows = gl_rows
        self.ap_rows = ap_rows
        self.rowcount = 0
        self._last = ""

    def execute(self, sql, params=None):
        self.sink.append((sql, params))
        self._last = sql

    def fetchone(self):
        return self.wm_row

    def fetchall(self):
        if "test_GLTable" in self._last:
            return self.gl_rows
        if "test_APBillTable" in self._last:
            return self.ap_rows
        return []

    def close(self):
        pass


class FakeConn:
    def __init__(self, wm_row=None, gl_rows=None, ap_rows=None):
        self.calls = []
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.wm_row = wm_row
        self.gl_rows = gl_rows or []
        self.ap_rows = ap_rows or []

    def cursor(self):
        return FakeCursor(self.calls, self.wm_row, self.gl_rows, self.ap_rows)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


class DeltaClient:
    def __init__(self, gl=None, ap=None, gl_keys=None, ap_keys=None, gl_err=None):
        self.gl = gl or []
        self.ap = ap or []
        self.gl_keys = gl_keys or []
        self.ap_keys = ap_keys or []
        self.gl_err = gl_err
        self.seen = {}

    def login(self):
        return "SID"

    def fetch_gl_delta(self, sid, since, until, page_size=2000, **k):
        self.seen["gl_since"] = since
        self.seen["gl_until"] = until
        return (self.gl, self.gl_err)

    def fetch_ap_delta(self, sid, since, until, page_size=2000, **k):
        self.seen["ap_since"] = since
        return (self.ap, None)

    def fetch_gl_keys(self, sid, s, e, page_size=2000, **k):
        self.seen["sweep_start"] = s
        self.seen["sweep_end"] = e
        return (self.gl_keys, None)

    def fetch_ap_keys(self, sid, s, e, page_size=2000, **k):
        self.seen["ap_keys_start"] = s
        self.seen["ap_keys_end"] = e
        return (self.ap_keys, None)


def _run(cfg, client, conn, today=date(2026, 7, 2), now=None):
    def connect(sql_cfg, secrets):
        return conn

    def factory(creds):
        return client

    return orchestrator.run(cfg, secrets={}, today=today, sage_client_factory=factory,
                            connect=connect, now=now or (lambda: datetime(2026, 7, 2, 9, 0, 30)))


def _wm_updates(conn):
    return [c for c in conn.calls if WM in c[0] and "UPDATE" in c[0].upper()]


def test_upsert_loads_delta_advances_watermark_no_wipe():
    conn = FakeConn(wm_row=(datetime(2026, 7, 1, 0, 0, 0),))
    client = DeltaClient(gl=[{"RECORDNO": "1", "LINE_NO": "1"}, {"RECORDNO": "2", "LINE_NO": "1"}],
                         ap=[{"RECORDNO": "9"}])
    res = _run(base_cfg("upsert"), client, conn)
    env = res.environments["Evergreen"]
    assert env["gl_loaded"] == 2 and env["ap_loaded"] == 1
    assert not [c for c in conn.calls if "DELETE FROM" in c[0].upper()]        # no wipe, no sweep
    assert any("test_AddGLDetailBulk" in c[0] for c in conn.calls)
    cutoff = datetime(2026, 7, 2, 9, 0, 0)                                     # now truncated to minute
    assert any(cutoff in (c[1] or ()) for c in _wm_updates(conn))             # advanced to cutoff
    assert client.seen["gl_since"] == datetime(2026, 6, 30, 23, 0, 0)         # watermark - 1h grace
    assert conn.commits == 1 and conn.rollbacks == 0


def test_upsert_without_watermark_uses_window_floor():
    conn = FakeConn(wm_row=None)
    client = DeltaClient(gl=[], ap=[])
    _run(base_cfg("upsert"), client, conn)
    # daily window start for today 2026-07-02, lookback 3 = 2026-04-01
    assert client.seen["gl_since"] == datetime(2026, 4, 1, 0, 0, 0)


def test_sweep_mode_deletes_absent_keys_only():
    conn = FakeConn(gl_rows=[("100", "1"), ("999", "9")], ap_rows=[("200",)])
    client = DeltaClient(gl_keys=[{"RECORDNO": "100", "LINE_NO": "1"}],
                         ap_keys=[{"RECORDNO": "200"}])
    res = _run(base_cfg("sweep"), client, conn)
    dels = [c for c in conn.calls if "DELETE FROM test_GLTable" in c[0]]
    assert len(dels) == 1 and dels[0][1] == ("999", "9")
    assert not [c for c in conn.calls if "test_AddGLDetailBulk" in c[0]]       # no upsert
    assert not _wm_updates(conn)                                              # watermark not advanced
    assert conn.commits == 1


def test_sweep_upsert_does_both():
    conn = FakeConn(wm_row=(datetime(2026, 7, 1, 0, 0, 0),),
                    gl_rows=[("100", "1"), ("999", "9")], ap_rows=[("200",)])
    client = DeltaClient(gl=[{"RECORDNO": "1", "LINE_NO": "1"}],
                         gl_keys=[{"RECORDNO": "100", "LINE_NO": "1"}],
                         ap_keys=[{"RECORDNO": "200"}])
    _run(base_cfg("sweep-upsert"), client, conn)
    assert any("test_AddGLDetailBulk" in c[0] for c in conn.calls)             # upsert ran
    assert [c for c in conn.calls if "DELETE FROM test_GLTable" in c[0]]       # sweep ran
    assert _wm_updates(conn)                                                  # watermark advanced


def test_sweep_audit_records_delete_counts():
    cfg = base_cfg("sweep")
    cfg["sql"]["apbill_sweep_date_field"] = "When_Posted"       # enable windowed AP sweep
    conn = FakeConn(gl_rows=[("100", "1"), ("999", "9")], ap_rows=[("200",), ("201",)])
    client = DeltaClient(gl_keys=[{"RECORDNO": "100", "LINE_NO": "1"}],
                         ap_keys=[{"RECORDNO": "200"}])
    res = _run(cfg, client, conn)
    env = res.audit.environments[0]
    assert env.gl_deleted == 1 and env.ap_deleted == 1
    assert res.audit.to_dict()["totals"]["deleted"] == 2


def test_ap_sweep_skipped_without_date_field():
    # ACCUMULATE FIX: with no date column, AP sweep must be SKIPPED (never a full-table compare
    # that would delete historical bills Intacct no longer returns).
    conn = FakeConn(gl_rows=[("100", "1")], ap_rows=[("200",), ("201",)])
    client = DeltaClient(gl_keys=[{"RECORDNO": "100", "LINE_NO": "1"}], ap_keys=[{"RECORDNO": "200"}])
    _run(base_cfg("sweep"), client, conn)
    assert not [c for c in conn.calls if "DELETE FROM test_APBillTable" in c[0]]


def test_ap_sweep_windowed_when_date_field_configured():
    # With a date column, AP sweep windows on it [start,end] — old data outside the window untouched.
    cfg = base_cfg("sweep")
    cfg["sql"]["apbill_sweep_date_field"] = "When_Posted"
    conn = FakeConn(gl_rows=[("100", "1")], ap_rows=[("200",), ("201",)])
    client = DeltaClient(gl_keys=[{"RECORDNO": "100", "LINE_NO": "1"}], ap_keys=[{"RECORDNO": "200"}])
    _run(cfg, client, conn)
    assert client.seen["ap_keys_start"] == date(2026, 4, 1)             # run window start, not a 2000 floor
    dels = [c for c in conn.calls if "DELETE FROM test_APBillTable" in c[0]]
    assert dels and dels[0][1] == ("201",)


def test_sweep_skipped_when_key_pull_is_empty():
    # FIX #2: an empty (but error-free) key pull must NOT delete every in-scope row.
    conn = FakeConn(gl_rows=[("100", "1"), ("999", "9")], ap_rows=[("200",)])
    client = DeltaClient(gl_keys=[], ap_keys=[])
    _run(base_cfg("sweep"), client, conn)
    assert not [c for c in conn.calls if "DELETE FROM" in c[0].upper()]  # mass-delete guard


def test_upsert_runs_reconcile():
    # FIX #4: delta upsert must still run the reconcile procs (parity with full refresh).
    conn = FakeConn(wm_row=(datetime(2026, 7, 1, 0, 0, 0),))
    client = DeltaClient(gl=[{"RECORDNO": "1", "LINE_NO": "1"}], ap=[{"RECORDNO": "9"}])
    _run(base_cfg("upsert"), client, conn)
    assert any("test_SyncAPLinksToGL" in c[0] for c in conn.calls)
    assert any("test_UpdateVendorInfoInGL" in c[0] for c in conn.calls)


def test_delta_missing_watermark_table_is_isolated_not_a_crash():
    # FIX #5: a missing config key must degrade the env, not crash the whole run.
    cfg = base_cfg("upsert")
    del cfg["sql"]["watermark_table"]
    res = _run(cfg, DeltaClient(gl=[], ap=[]), FakeConn(wm_row=None))
    assert res.environments["Evergreen"]["error"] is not None


def test_delta_unsupported_load_strategy_marks_error():
    # FIX #7: delta must reject an unimplemented load_strategy like the full path does.
    cfg = base_cfg("upsert")
    cfg["sql"]["load_strategy"] = "staging_swap"
    res = _run(cfg, DeltaClient(gl=[], ap=[]), FakeConn(wm_row=None))
    assert res.environments["Evergreen"]["error"] is not None


def test_upsert_gl_error_skips_gl_watermark_but_advances_ap():
    conn = FakeConn(wm_row=(datetime(2026, 7, 1, 0, 0, 0),))
    client = DeltaClient(gl=[{"RECORDNO": "1", "LINE_NO": "1"}], ap=[{"RECORDNO": "9"}],
                         gl_err="boom")
    res = _run(base_cfg("upsert"), client, conn)
    env = res.environments["Evergreen"]
    assert env["gl_error"] == "boom"
    params = [p for c in _wm_updates(conn) for p in (c[1] or ())]
    assert "AP" in params and "GL" not in params          # AP advanced, GL held back (gate)
    assert conn.commits == 1 and not res.ok               # committed-degraded, flagged


def test_effective_now_respects_injected_clock():
    from datetime import datetime as _dt
    fixed = _dt(2026, 7, 2, 9, 0, 30)
    assert orchestrator._effective_now(lambda: fixed, "America/Los_Angeles", injected=True) == fixed


def test_effective_now_localizes_to_company_tz_when_not_injected():
    from datetime import datetime as _dt
    dt = orchestrator._effective_now(lambda: _dt(2000, 1, 1), "America/Los_Angeles", injected=False)
    assert isinstance(dt, _dt) and dt.tzinfo is None and dt.year >= 2026    # real wall-clock in tz
