"""W-delta integration — orchestrator delta modes (specs/13 §13.4/§13.7). Test-first.

Modes: upsert (delta only), sweep (delete-sweep only), sweep-upsert (both). No bulk wipe.
Watermark advances only on a clean object fetch (completeness gate, §13.11).

specs/13 §13.16 (memory-safety): the delta path MUST process its window in calendar-month
sub-chunks (extract -> apply -> release) so peak memory <= one chunk. The fakes here are
WINDOW-AWARE — Sage delta records carry a `_mod` (WHENMODIFIED) and keys/SQL rows carry a
`_date`, so a chunked run returns exactly the same union a single pass would, and the
per-chunk interleaving (fetch, apply, fetch, apply, ...) is observable.
"""
from datetime import date, datetime

import pytest

from intacct_etl import dates, orchestrator

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


def _clean(r):
    """A copy of a fake record without the test-only `_mod`/`_date` markers (so the real
    transform.encode_page never sees an un-JSON-able datetime)."""
    return {k: v for k, v in r.items() if not k.startswith("_")}


def _mod_in(r, since, until):
    m = r.get("_mod")
    return True if m is None else (since < m <= until)     # strict > lower / <= upper (§13.16)


def _date_in(r, s, e):
    d = r.get("_date")
    return True if d is None else (s <= d <= e)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0
        self._sql = ""
        self._params = None

    def execute(self, sql, params=None):
        self.conn.calls.append((sql, params))
        self._sql, self._params = sql, params
        u = sql.strip().upper()
        if u.startswith("DELETE FROM TEST_GLTABLE"):
            self.conn.events.append("Dgl")
        elif u.startswith("DELETE FROM TEST_APBILLTABLE"):
            self.conn.events.append("Dap")
        elif "TEST_ADDGLDETAILBULK" in u:
            self.conn.events.append("Lgl")
        elif "TEST_ADDAPBILLBULK" in u:
            self.conn.events.append("Lap")

    def fetchone(self):
        return self.conn.wm_row

    def fetchall(self):
        # Window-aware SELECT for the sweep: return only rows inside the queried [start, end].
        u = self._sql.strip().upper()
        p = self._params or ()
        if u.startswith("SELECT") and "FROM TEST_GLTABLE" in u:
            start, end = p[0], p[1]
            return [(r[0], r[1]) for r in self.conn.gl_rows if start <= r[2] <= end]
        if u.startswith("SELECT") and "FROM TEST_APBILLTABLE" in u:
            start, end = p[0], p[1]
            return [(r[0],) for r in self.conn.ap_rows if start <= r[1] <= end]
        return []

    def close(self):
        pass


class FakeConn:
    def __init__(self, wm_row=None, gl_rows=None, ap_rows=None, events=None):
        self.calls = []
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.wm_row = wm_row
        self.gl_rows = gl_rows or []     # (Record_No, Line_No, date)
        self.ap_rows = ap_rows or []     # (Record_No, date)
        self.events = events if events is not None else []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


class DeltaClient:
    """Window-aware fake Sage client. Records every fetch (per chunk) and filters by window."""

    def __init__(self, gl=None, ap=None, gl_keys=None, ap_keys=None, gl_err=None, events=None):
        self.gl = gl or []               # dicts w/ optional "_mod" datetime
        self.ap = ap or []
        self.gl_keys = gl_keys or []     # dicts w/ optional "_date" date
        self.ap_keys = ap_keys or []
        self.gl_err = gl_err
        self.seen = {}                   # first since/start + last until/end (back-compat asserts)
        self.calls = {"gl_delta": [], "ap_delta": [], "gl_keys": [], "ap_keys": []}
        self.events = events if events is not None else []

    def login(self):
        return "SID"

    def fetch_gl_delta(self, sid, since, until, page_size=2000, **k):
        self.calls["gl_delta"].append((since, until))
        self.seen.setdefault("gl_since", since)
        self.seen["gl_until"] = until
        self.events.append("Fgl")
        return ([_clean(r) for r in self.gl if _mod_in(r, since, until)], self.gl_err)

    def fetch_ap_delta(self, sid, since, until, page_size=2000, **k):
        self.calls["ap_delta"].append((since, until))
        self.seen.setdefault("ap_since", since)
        self.seen["ap_until"] = until
        return ([_clean(r) for r in self.ap if _mod_in(r, since, until)], None)

    def fetch_gl_keys(self, sid, s, e, page_size=2000, **k):
        self.calls["gl_keys"].append((s, e))
        self.seen.setdefault("sweep_start", s)
        self.seen["sweep_end"] = e
        self.events.append("Fgk")
        return ([r for r in self.gl_keys if _date_in(r, s, e)], None)

    def fetch_ap_keys(self, sid, s, e, page_size=2000, **k):
        self.calls["ap_keys"].append((s, e))
        self.seen.setdefault("ap_keys_start", s)
        self.seen["ap_keys_end"] = e
        return ([r for r in self.ap_keys if _date_in(r, s, e)], None)


def _run(cfg, client, conn, today=date(2026, 7, 2), now=None):
    def connect(sql_cfg, secrets):
        return conn

    def factory(creds):
        return client

    return orchestrator.run(cfg, secrets={}, today=today, sage_client_factory=factory,
                            connect=connect, now=now or (lambda: datetime(2026, 7, 2, 9, 0, 30)))


def _wm_updates(conn):
    return [c for c in conn.calls if WM in c[0] and "UPDATE" in c[0].upper()]


def _gl_deletes(conn):
    return [c for c in conn.calls if "DELETE FROM test_GLTable" in c[0]]


# ----------------------------------------------------------------------------------------------
# Behaviour (unchanged contract) — now over a window-aware, chunked path
# ----------------------------------------------------------------------------------------------

def test_upsert_loads_delta_advances_watermark_no_wipe():
    conn = FakeConn(wm_row=(datetime(2026, 7, 1, 0, 0, 0),))
    client = DeltaClient(gl=[{"RECORDNO": "1", "LINE_NO": "1", "_mod": datetime(2026, 7, 1, 12, 0)},
                             {"RECORDNO": "2", "LINE_NO": "1", "_mod": datetime(2026, 7, 1, 13, 0)}],
                         ap=[{"RECORDNO": "9", "_mod": datetime(2026, 7, 1, 12, 0)}])
    res = _run(base_cfg("upsert"), client, conn)
    env = res.environments["Evergreen"]
    assert env["gl_loaded"] == 2 and env["ap_loaded"] == 1
    assert not [c for c in conn.calls if "DELETE FROM" in c[0].upper()]        # no wipe, no sweep
    assert any("test_AddGLDetailBulk" in c[0] for c in conn.calls)
    cutoff = datetime(2026, 7, 2, 9, 0, 0)                                     # now truncated to minute
    assert any(cutoff in (c[1] or ()) for c in _wm_updates(conn))             # advanced to cutoff
    assert client.seen["gl_since"] == datetime(2026, 6, 30, 23, 0, 0)         # first chunk = wm - 1h grace
    assert client.seen["gl_until"] == cutoff                                  # last chunk ends at cutoff
    assert conn.commits == 1 and conn.rollbacks == 0


def test_upsert_without_watermark_uses_window_floor():
    conn = FakeConn(wm_row=None)
    client = DeltaClient(gl=[], ap=[])
    _run(base_cfg("upsert"), client, conn)
    # daily window start for today 2026-07-02, lookback 3 = 2026-04-01 -> first chunk since = floor
    assert client.seen["gl_since"] == datetime(2026, 4, 1, 0, 0, 0)


def test_sweep_mode_deletes_absent_keys_only():
    jun = date(2026, 6, 15)
    conn = FakeConn(gl_rows=[("100", "1", jun), ("999", "9", jun)], ap_rows=[("200", jun)])
    client = DeltaClient(gl_keys=[{"RECORDNO": "100", "LINE_NO": "1", "_date": jun}],
                         ap_keys=[{"RECORDNO": "200", "_date": jun}])
    _run(base_cfg("sweep"), client, conn)
    dels = _gl_deletes(conn)
    assert len(dels) == 1 and dels[0][1] == ("999", "9")
    assert not [c for c in conn.calls if "test_AddGLDetailBulk" in c[0]]       # no upsert
    assert not _wm_updates(conn)                                              # watermark not advanced
    assert conn.commits == 1


def test_sweep_upsert_does_both():
    jun = date(2026, 6, 15)
    conn = FakeConn(wm_row=(datetime(2026, 7, 1, 0, 0, 0),),
                    gl_rows=[("100", "1", jun), ("999", "9", jun)], ap_rows=[("200", jun)])
    client = DeltaClient(gl=[{"RECORDNO": "1", "LINE_NO": "1", "_mod": datetime(2026, 7, 1, 12, 0)}],
                         gl_keys=[{"RECORDNO": "100", "LINE_NO": "1", "_date": jun}],
                         ap_keys=[{"RECORDNO": "200", "_date": jun}])
    _run(base_cfg("sweep-upsert"), client, conn)
    assert any("test_AddGLDetailBulk" in c[0] for c in conn.calls)             # upsert ran
    assert _gl_deletes(conn)                                                   # sweep ran
    assert _wm_updates(conn)                                                   # watermark advanced


def test_sweep_audit_records_delete_counts():
    jun = date(2026, 6, 15)
    cfg = base_cfg("sweep")
    cfg["sql"]["apbill_sweep_date_field"] = "When_Posted"       # enable windowed AP sweep
    conn = FakeConn(gl_rows=[("100", "1", jun), ("999", "9", jun)],
                    ap_rows=[("200", jun), ("201", jun)])
    client = DeltaClient(gl_keys=[{"RECORDNO": "100", "LINE_NO": "1", "_date": jun}],
                         ap_keys=[{"RECORDNO": "200", "_date": jun}])
    res = _run(cfg, client, conn)
    env = res.audit.environments[0]
    assert env.gl_deleted == 1 and env.ap_deleted == 1
    assert res.audit.to_dict()["totals"]["deleted"] == 2


def test_ap_sweep_skipped_without_date_field():
    # ACCUMULATE FIX: with no date column, AP sweep must be SKIPPED (never a full-table compare
    # that would delete historical bills Intacct no longer returns).
    jun = date(2026, 6, 15)
    conn = FakeConn(gl_rows=[("100", "1", jun)], ap_rows=[("200", jun), ("201", jun)])
    client = DeltaClient(gl_keys=[{"RECORDNO": "100", "LINE_NO": "1", "_date": jun}],
                         ap_keys=[{"RECORDNO": "200", "_date": jun}])
    _run(base_cfg("sweep"), client, conn)
    assert not [c for c in conn.calls if "DELETE FROM test_APBillTable" in c[0]]


def test_ap_sweep_windowed_when_date_field_configured():
    # With a date column, AP sweep windows on it — old data outside the window untouched.
    jun = date(2026, 6, 15)
    cfg = base_cfg("sweep")
    cfg["sql"]["apbill_sweep_date_field"] = "When_Posted"
    conn = FakeConn(gl_rows=[("100", "1", jun)], ap_rows=[("200", jun), ("201", jun)])
    client = DeltaClient(gl_keys=[{"RECORDNO": "100", "LINE_NO": "1", "_date": jun}],
                         ap_keys=[{"RECORDNO": "200", "_date": jun}])
    _run(cfg, client, conn)
    assert client.seen["ap_keys_start"] == date(2026, 4, 1)             # first chunk = run window start
    dels = [c for c in conn.calls if "DELETE FROM test_APBillTable" in c[0]]
    assert dels and dels[0][1] == ("201",)


def test_sweep_skipped_when_key_pull_is_empty():
    # An empty (but error-free) key pull must NOT delete every in-scope row (mass-delete guard).
    jun = date(2026, 6, 15)
    conn = FakeConn(gl_rows=[("100", "1", jun), ("999", "9", jun)], ap_rows=[("200", jun)])
    client = DeltaClient(gl_keys=[], ap_keys=[])
    _run(base_cfg("sweep"), client, conn)
    assert not [c for c in conn.calls if "DELETE FROM" in c[0].upper()]


def test_upsert_runs_reconcile():
    # delta upsert must still run the reconcile procs (parity with full refresh).
    conn = FakeConn(wm_row=(datetime(2026, 7, 1, 0, 0, 0),))
    client = DeltaClient(gl=[{"RECORDNO": "1", "LINE_NO": "1", "_mod": datetime(2026, 7, 1, 12, 0)}],
                         ap=[{"RECORDNO": "9", "_mod": datetime(2026, 7, 1, 12, 0)}])
    _run(base_cfg("upsert"), client, conn)
    assert any("test_SyncAPLinksToGL" in c[0] for c in conn.calls)
    assert any("test_UpdateVendorInfoInGL" in c[0] for c in conn.calls)


def test_delta_missing_watermark_table_is_isolated_not_a_crash():
    cfg = base_cfg("upsert")
    del cfg["sql"]["watermark_table"]
    res = _run(cfg, DeltaClient(gl=[], ap=[]), FakeConn(wm_row=None))
    assert res.environments["Evergreen"]["error"] is not None


def test_delta_unsupported_load_strategy_marks_error():
    cfg = base_cfg("upsert")
    cfg["sql"]["load_strategy"] = "staging_swap"
    res = _run(cfg, DeltaClient(gl=[], ap=[]), FakeConn(wm_row=None))
    assert res.environments["Evergreen"]["error"] is not None


def test_upsert_gl_error_skips_gl_watermark_but_advances_ap():
    conn = FakeConn(wm_row=(datetime(2026, 7, 1, 0, 0, 0),))
    client = DeltaClient(gl=[{"RECORDNO": "1", "LINE_NO": "1", "_mod": datetime(2026, 7, 1, 12, 0)}],
                         ap=[{"RECORDNO": "9", "_mod": datetime(2026, 7, 1, 12, 0)}],
                         gl_err="boom")
    res = _run(base_cfg("upsert"), client, conn)
    env = res.environments["Evergreen"]
    assert env["gl_error"] == "boom"
    params = [p for c in _wm_updates(conn) for p in (c[1] or ())]
    assert "AP" in params and "GL" not in params          # AP advanced, GL held back (gate)
    assert conn.commits == 1 and not res.ok               # committed-degraded, flagged


# ----------------------------------------------------------------------------------------------
# specs/13 §13.16 — bounded-memory chunking (tiling + per-chunk interleaving)
# ----------------------------------------------------------------------------------------------

def test_upsert_chunks_whenmodified_range_into_calendar_months():
    # Far-behind watermark -> a wide (since, cutoff] range MUST be pulled month-by-month, not in one
    # in-memory pass. The per-chunk sub-windows tile the whole range exactly (shared boundaries).
    conn = FakeConn(wm_row=(datetime(2026, 4, 1, 0, 0, 0),))
    client = DeltaClient(gl=[], ap=[])
    _run(base_cfg("upsert"), client, conn)
    calls = client.calls["gl_delta"]
    since = datetime(2026, 3, 31, 23, 0, 0)                 # wm - 1h grace
    cutoff = datetime(2026, 7, 2, 9, 0, 0)
    assert calls == dates.month_chunks_dt(since, cutoff)    # exact monthly tiling
    assert len(calls) == 5
    assert calls[0][0] == since and calls[-1][1] == cutoff
    for (s1, e1), (s2, e2) in zip(calls, calls[1:]):
        assert e1 == s2                                     # contiguous, no gap/overlap


def test_sweep_chunks_date_window_into_calendar_months():
    conn = FakeConn(gl_rows=[], ap_rows=[])
    client = DeltaClient(gl_keys=[], ap_keys=[])
    _run(base_cfg("sweep"), client, conn)
    # daily window today 2026-07-02 = 2026-04-01 .. 2026-07-31
    assert client.calls["gl_keys"] == dates.month_chunks(date(2026, 4, 1), date(2026, 7, 31))
    assert len(client.calls["gl_keys"]) == 4


def test_sweep_streams_fetch_then_delete_per_chunk_bounded_memory():
    # Memory-safety proof: for a 3-month window with one missing row per month, the event trace
    # must be fetch,delete,fetch,delete,... (apply-then-release) — NEVER all fetches then all
    # deletes (which would hold every chunk's keys live at once).
    apr, may, jun = date(2026, 4, 15), date(2026, 5, 15), date(2026, 6, 15)
    events = []
    conn = FakeConn(events=events,
                    gl_rows=[("A1", "1", apr), ("A2", "1", apr),
                             ("M1", "1", may), ("M2", "1", may),
                             ("J1", "1", jun), ("J2", "1", jun)])
    client = DeltaClient(events=events,
                         gl_keys=[{"RECORDNO": "A1", "LINE_NO": "1", "_date": apr},
                                  {"RECORDNO": "M1", "LINE_NO": "1", "_date": may},
                                  {"RECORDNO": "J1", "LINE_NO": "1", "_date": jun}])
    cfg = base_cfg("sweep", run={"window_override": {"start": "04/01/2026", "end": "06/30/2026"}})
    _run(cfg, client, conn)
    assert events == ["Fgk", "Dgl", "Fgk", "Dgl", "Fgk", "Dgl"]


def test_upsert_streams_fetch_then_load_per_chunk_bounded_memory():
    events = []
    conn = FakeConn(wm_row=(datetime(2026, 7, 1, 0, 0, 0),), events=events)   # -> 2 chunks (Jun, Jul)
    client = DeltaClient(
        events=events,
        gl=[{"RECORDNO": "1", "LINE_NO": "1", "_mod": datetime(2026, 6, 30, 23, 30)},   # chunk 1
            {"RECORDNO": "2", "LINE_NO": "1", "_mod": datetime(2026, 7, 1, 12, 0)}],     # chunk 2
        ap=[])
    _run(base_cfg("upsert"), client, conn)
    gl_events = [e for e in events if e in ("Fgl", "Lgl")]
    assert gl_events == ["Fgl", "Lgl", "Fgl", "Lgl"]       # fetch->load interleaved, one chunk live


def test_chunked_sweep_parity_deletes_exactly_the_missing_union():
    # Parity: the chunked sweep deletes precisely the rows missing across the whole window — same
    # set a single-pass sweep would (one missing row per month, none wrongly deleted, none missed).
    apr, may, jun = date(2026, 4, 15), date(2026, 5, 15), date(2026, 6, 15)
    conn = FakeConn(gl_rows=[("A1", "1", apr), ("A2", "1", apr),
                             ("M1", "1", may), ("M2", "1", may),
                             ("J1", "1", jun), ("J2", "1", jun)])
    client = DeltaClient(gl_keys=[{"RECORDNO": "A1", "LINE_NO": "1", "_date": apr},
                                  {"RECORDNO": "M1", "LINE_NO": "1", "_date": may},
                                  {"RECORDNO": "J1", "LINE_NO": "1", "_date": jun}])
    cfg = base_cfg("sweep", run={"window_override": {"start": "04/01/2026", "end": "06/30/2026"}})
    res = _run(cfg, client, conn)
    deleted = {c[1] for c in _gl_deletes(conn)}
    assert deleted == {("A2", "1"), ("M2", "1"), ("J2", "1")}
    assert res.audit.environments[0].gl_deleted == 3


def test_effective_now_respects_injected_clock():
    from datetime import datetime as _dt
    fixed = _dt(2026, 7, 2, 9, 0, 30)
    assert orchestrator._effective_now(lambda: fixed, "America/Los_Angeles", injected=True) == fixed


def test_effective_now_localizes_to_company_tz_when_not_injected():
    from datetime import datetime as _dt
    dt = orchestrator._effective_now(lambda: _dt(2000, 1, 1), "America/Los_Angeles", injected=False)
    assert isinstance(dt, _dt) and dt.tzinfo is None and dt.year >= 2026    # real wall-clock in tz
