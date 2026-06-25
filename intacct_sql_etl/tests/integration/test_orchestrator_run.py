"""W5 — full orchestrator run with injected fakes (specs/05). Config-driven; offline.
Covers: run_type/window derivation + overrides, per-env loop + isolation, transaction
commit/rollback, reconcile granularity, and config knobs flowing through. Test-first."""
from datetime import date

import pytest

from intacct_etl import orchestrator, sql_loader

pytestmark = pytest.mark.integration

SQL_CFG = {
    "gl_table": "test_GLTable", "apbill_table": "test_APBillTable",
    "gl_delete_date_field": "Entry_Date", "apbill_full_reload": True,
    "proc_add_gl": "test_AddGLDetailBulk", "proc_add_ap": "test_AddAPBillBulk",
    "proc_sync_links": "test_SyncAPLinksToGL", "proc_update_vendor": "test_UpdateVendorInfoInGL",
    "load_strategy": "transaction",
}


def base_cfg(**over):
    cfg = {
        "run": {"test_mode": True, "daily_lookback_months": 3,
                "reconcile_granularity": "per_chunk"},
        "environments": [{"name": "Evergreen", "sage_creds_key": "K1",
                          "sender_id": "s", "sender_password": "p", "user_id": "u",
                          "user_password": "p", "company_id": "c"}],
        "sage": {"url": "https://sage", "page_size": 2000},
        "gl": {}, "ap": {},
        "sql": dict(SQL_CFG),
        "retry": {}, "log": {"sink": "stdout"},
    }
    cfg["run"].update(over.get("run", {}))
    if "environments" in over:
        cfg["environments"] = over["environments"]
    if "sql" in over:
        cfg["sql"].update(over["sql"])
    return cfg


class FakeCursor:
    def __init__(self, sink):
        self.sink = sink

    def execute(self, sql, params=None):
        self.sink.append((sql, params))

    def close(self):
        pass


class FakeConn:
    def __init__(self, rollback_raises=False):
        self.calls = []
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self._rollback_raises = rollback_raises

    def cursor(self):
        return FakeCursor(self.calls)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1
        if self._rollback_raises:
            raise RuntimeError("rollback failed")

    def close(self):
        self.closes += 1


class FakeClient:
    """Returns `per_chunk` GL/AP records for each window; optionally raises."""

    def __init__(self, creds, gl_per_chunk=3, ap_per_chunk=2, raise_on_gl=False):
        self.creds = creds
        self.gl_per_chunk = gl_per_chunk
        self.ap_per_chunk = ap_per_chunk
        self.raise_on_gl = raise_on_gl
        self.logins = 0

    def login(self):
        self.logins += 1
        return "SID"

    def fetch_gl(self, sid, s, e, page_size=2000, **k):
        if self.raise_on_gl:
            raise RuntimeError("sage boom")
        return [{"RECORDNO": f"{s}-{i}"} for i in range(self.gl_per_chunk)], None

    def fetch_ap(self, sid, s, e, page_size=2000, **k):
        return [{"RECORDNO": f"AP-{s}-{i}"} for i in range(self.ap_per_chunk)], None


def _run(cfg, *, conns=None, client=None, today=date(2026, 6, 24)):
    conns = conns or []

    def connect(sql_cfg, secrets):
        c = FakeConn()
        conns.append(c)
        return c

    def factory(creds):
        return client or FakeClient(creds)

    res = orchestrator.run(cfg, secrets={}, today=today,
                           sage_client_factory=factory, connect=connect)
    return res, conns


def test_run_daily_window_and_counts():
    cfg = base_cfg()
    res, conns = _run(cfg)
    assert res.run_type == "daily"
    assert res.start == date(2026, 3, 1) and res.end == date(2026, 6, 30)
    env = res.environments["Evergreen"]
    # 4 month chunks * 3 GL + 4 * 2 AP
    assert env["gl_records"] == 12 and env["gl_loaded"] == 12
    assert env["ap_records"] == 8 and env["ap_loaded"] == 8
    assert env["error"] is None
    assert conns[0].commits == 1 and conns[0].rollbacks == 0


def test_run_type_monthly_on_first():
    res, _ = _run(base_cfg(), today=date(2026, 4, 1))
    assert res.run_type == "monthly"
    assert res.start == date(2025, 1, 1) and res.end == date(2026, 4, 30)


def test_window_override():
    cfg = base_cfg(run={"window_override": {"start": "02/01/2026", "end": "02/28/2026"}})
    res, _ = _run(cfg)
    assert res.start == date(2026, 2, 1) and res.end == date(2026, 2, 28)


def test_run_type_override_forces_monthly():
    cfg = base_cfg(run={"run_type_override": "monthly"})
    res, _ = _run(cfg, today=date(2026, 6, 24))
    assert res.run_type == "monthly"


def test_reconcile_granularity_per_window_runs_once():
    cfg = base_cfg(run={"reconcile_granularity": "per_window"})
    res, conns = _run(cfg)
    sync_calls = [c for c in conns[0].calls if "test_SyncAPLinksToGL" in c[0]]
    assert len(sync_calls) == 1                        # once, not per-chunk


def test_reconcile_granularity_per_chunk_runs_per_chunk():
    cfg = base_cfg(run={"reconcile_granularity": "per_chunk"})
    res, conns = _run(cfg)
    sync_calls = [c for c in conns[0].calls if "test_SyncAPLinksToGL" in c[0]]
    assert len(sync_calls) == 4                        # 4 chunks


def test_env_failure_isolated_and_rolled_back():
    cfg = base_cfg(environments=[
        {"name": "Bad", "sage_creds_key": "K1", "sender_id": "s", "sender_password": "p",
         "user_id": "u", "user_password": "p", "company_id": "c"},
        {"name": "Good", "sage_creds_key": "K2", "sender_id": "s", "sender_password": "p",
         "user_id": "u", "user_password": "p", "company_id": "c"},
    ])
    conns = []

    def connect(sql_cfg, secrets):
        c = FakeConn()
        conns.append(c)
        return c

    clients = {"Bad": FakeClient({}, raise_on_gl=True), "Good": FakeClient({})}

    def factory(creds):
        # creds carries no name; map by call order
        return clients["Bad"] if len(conns) == 1 else clients["Good"]

    res = orchestrator.run(cfg, secrets={}, today=date(2026, 6, 24),
                           sage_client_factory=factory, connect=connect)
    assert res.environments["Bad"]["error"] is not None
    assert conns[0].rollbacks == 1 and conns[0].commits == 0
    assert res.environments["Good"]["error"] is None
    assert conns[1].commits == 1
    assert not res.ok                                  # overall degraded


def test_staging_swap_not_implemented_marks_error():
    cfg = base_cfg(sql={"load_strategy": "staging_swap"})
    res, _ = _run(cfg)
    assert res.environments["Evergreen"]["error"] is not None


def test_run_closes_connection_on_success():
    # CR-01: connection must be closed deterministically (no leak)
    cfg = base_cfg()
    res, conns = _run(cfg)
    assert conns[0].closes == 1


def test_run_closes_connection_on_failure():
    # CR-01: connection closed even when the env fails
    cfg = base_cfg()
    res, conns = _run(cfg, client=FakeClient({}, raise_on_gl=True))
    assert conns[0].closes == 1


def test_rollback_failure_is_recorded_not_swallowed():
    # CR-03: a rollback failure must be surfaced, never silently passed
    cfg = base_cfg()
    conns = []

    def connect(sql_cfg, secrets):
        c = FakeConn(rollback_raises=True)
        conns.append(c)
        return c

    def factory(creds):
        return FakeClient(creds, raise_on_gl=True)

    res = orchestrator.run(cfg, secrets={}, today=date(2026, 6, 24),
                           sage_client_factory=factory, connect=connect)
    entry = res.environments["Evergreen"]
    assert entry["error"] is not None
    assert entry["rollback_error"] is not None
    assert conns[0].closes == 1


def test_query_options_flow_from_config():
    # CR-06: gl/ap query options must be passed from config (not hardcoded defaults)
    seen = {}

    class RecordingClient(FakeClient):
        def fetch_gl(self, sid, s, e, page_size=2000, **k):
            seen.setdefault("gl", k)
            return [], None

        def fetch_ap(self, sid, s, e, page_size=2000, **k):
            seen.setdefault("ap", k)
            return [], None

    cfg = base_cfg()
    cfg["gl"] = {"date_field": "ENTRY_DATE", "book_filter": "CASH",
                 "order_by": ["RECORDNO"], "select_fields": ["RECORDNO"]}
    cfg["ap"] = {"date_field": "WHENCREATED", "show_private": False}
    cfg["sage"]["dtd_version"] = "2.1"
    cfg["run"]["date_format"] = "%Y-%m-%d"
    _run(cfg, client=RecordingClient({}))
    assert seen["gl"]["date_field"] == "ENTRY_DATE"
    assert seen["gl"]["book_filter"] == "CASH"
    assert seen["gl"]["fields"] == ["RECORDNO"]
    assert seen["gl"]["dtd_version"] == "2.1"
    assert seen["gl"]["date_format"] == "%Y-%m-%d"
    assert seen["ap"]["date_field"] == "WHENCREATED"
    assert seen["ap"]["show_private"] is False
    assert "fields" not in seen["ap"]              # AP picks probe/page set itself


def test_settle_sleep_before_reconcile():
    # CR-18: configurable settle pause before reconcile procs
    slept = []
    cfg = base_cfg(run={"reconcile_granularity": "per_window"})
    cfg["retry"] = {"reconcile_settle_s": 3}

    conns = []

    def connect(sql_cfg, secrets):
        c = FakeConn()
        conns.append(c)
        return c

    orchestrator.run(cfg, secrets={}, today=date(2026, 6, 24),
                     sage_client_factory=lambda creds: FakeClient(creds),
                     connect=connect, sleep=slept.append)
    assert 3 in slept


def test_chunk_errors_are_aggregated():
    # CR-13: per-chunk errors must accumulate, not overwrite
    class ErroringClient(FakeClient):
        def fetch_gl(self, sid, s, e, page_size=2000, **k):
            return [], f"gl err {s}"

    cfg = base_cfg()  # daily -> 4 chunks
    res, _ = _run(cfg, client=ErroringClient({}))
    entry = res.environments["Evergreen"]
    assert len(entry["gl_errors"]) == 4
    assert entry["gl_error"] is not None        # scalar still set for back-compat
