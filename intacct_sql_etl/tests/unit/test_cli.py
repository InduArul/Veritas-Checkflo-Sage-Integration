"""CLI argument parsing + wiring (injectable deps). Test-first."""
from datetime import date

from intacct_etl import cli


def test_parse_args_defaults():
    ns = cli.parse_args(["--config", "c.yaml"])
    assert ns.config == "c.yaml"
    assert ns.today is None


def test_parse_args_backfill():
    ns = cli.parse_args(["--config", "c.yaml", "--today", "2026-06-24",
                         "--run-type", "monthly"])
    assert ns.today == "2026-06-24"
    assert ns.run_type == "monthly"


def test_parse_args_mode():
    ns = cli.parse_args(["--config", "c.yaml", "--mode", "sweep-upsert"])
    assert ns.mode == "sweep-upsert"


def test_main_sets_mode_in_run_config():
    captured = {}

    class R:
        ok = True
        environments = {}

    def fake_runner(cfg, secrets, *, today, **kw):
        captured["mode"] = cfg["run"].get("mode")
        return R()

    cli.main(["--config", "x.yaml", "--mode", "upsert"],
             load_config=lambda p: {"run": {}, "log": {"sink": "stdout"}},
             runner=fake_runner, load_secrets=lambda: {})
    assert captured["mode"] == "upsert"


def test_main_passes_secret_provider_to_runner():
    captured = {}

    class R:
        ok = True
        environments = {}
        audit = None

    def fake_runner(cfg, secrets, *, today, **kw):
        captured["secrets"] = secrets
        return R()

    cli.main(["--config", "x.yaml"],
             load_config=lambda p: {"run": {}, "log": {"sink": "stdout"}},
             runner=fake_runner, load_secrets=lambda: {"A": "1"})
    assert getattr(captured["secrets"], "is_secret_provider", False)   # a provider, not a raw dict


def test_main_preflight_failure_returns_2_and_skips_runner():
    ran = {"runner": False}

    def fake_runner(*a, **k):
        ran["runner"] = True
        return None

    def boom_connect(sql_cfg, secrets):
        raise RuntimeError("Login timeout expired")

    rc = cli.main(["--config", "x.yaml"],
                  load_config=lambda p: {"run": {}, "log": {"sink": "stdout"}, "sql": {"server": "x"}},
                  runner=fake_runner, load_secrets=lambda: {}, connect=boom_connect)
    assert rc == 2 and ran["runner"] is False


def test_main_notifies_after_run():
    events = []

    class FakeNotifier:
        def notify(self, result):
            events.append(("notify", getattr(result, "ok", None)))

        def alert(self, subject, body):
            events.append(("alert", subject))

    class R:
        ok = True
        environments = {}
        audit = None

    cli.main(["--config", "x.yaml"],
             load_config=lambda p: {"run": {}, "log": {"sink": "stdout"}},
             runner=lambda *a, **k: R(), load_secrets=lambda: {},
             make_notifier=lambda c, s: FakeNotifier())
    assert ("notify", True) in events


def test_main_alerts_on_preflight_failure():
    events = []

    class FakeNotifier:
        def notify(self, result):
            events.append("notify")

        def alert(self, subject, body):
            events.append(("alert", subject))

    def boom_connect(sql_cfg, secrets):
        raise RuntimeError("Login failed for user")

    rc = cli.main(["--config", "x.yaml"],
                  load_config=lambda p: {"run": {}, "log": {"sink": "stdout"}, "sql": {"server": "x"}},
                  runner=lambda *a, **k: None, load_secrets=lambda: {}, connect=boom_connect,
                  make_notifier=lambda c, s: FakeNotifier())
    assert rc == 2
    assert any(isinstance(e, tuple) and e[0] == "alert" for e in events)
    assert "notify" not in events                       # run never happened


def test_main_preflight_ok_runs_runner():
    class OKCur:
        def execute(self, s, p=None):
            pass

        def fetchone(self):
            return (1,)

        def close(self):
            pass

    class OKConn:
        def cursor(self):
            return OKCur()

        def close(self):
            pass

    class R:
        ok = True
        environments = {}
        audit = None

    ran = {}

    def fake_runner(cfg, secrets, *, today, **kw):
        ran["ok"] = True
        return R()

    rc = cli.main(["--config", "x.yaml"],
                  load_config=lambda p: {"run": {}, "log": {"sink": "stdout"}, "sql": {"server": "x"}},
                  runner=fake_runner, load_secrets=lambda: {}, connect=lambda c, s: OKConn())
    assert ran.get("ok") and rc == 0


def test_main_wires_runner_with_parsed_today():
    captured = {}

    class R:
        ok = True
        environments = {}

    def fake_load_config(path):
        captured["path"] = path
        return {"run": {}, "log": {"sink": "stdout"}}

    def fake_runner(cfg, secrets, *, today, **kw):
        captured["today"] = today
        return R()

    rc = cli.main(["--config", "x.yaml", "--today", "2026-06-24"],
                  load_config=fake_load_config, runner=fake_runner, load_secrets=lambda: {})
    assert captured["path"] == "x.yaml"
    assert captured["today"] == date(2026, 6, 24)
    assert rc == 0


def test_main_returns_nonzero_when_degraded():
    class R:
        ok = False
        environments = {"E": {"error": "boom"}}

    rc = cli.main(["--config", "x.yaml"],
                  load_config=lambda p: {"run": {}, "log": {"sink": "stdout"}},
                  runner=lambda *a, **k: R(), load_secrets=lambda: {})
    assert rc != 0


def test_main_persists_audit_trail():
    # specs/12: the entrypoint persists the run's audit trail via the configured sink
    captured = {}

    class R:
        ok = True
        environments = {}
        audit = object()                          # sentinel audit record

    def fake_runner(cfg, secrets, *, today, **kw):
        return R()

    def fake_persist_audit(run_audit, audit_cfg):
        captured["audit"] = run_audit
        captured["cfg"] = audit_cfg
        return "audit.json"

    rc = cli.main(["--config", "x.yaml", "--today", "2026-06-24"],
                  load_config=lambda p: {"run": {}, "log": {"sink": "stdout"},
                                         "audit": {"enabled": True, "sink": "stdout"}},
                  runner=fake_runner, load_secrets=lambda: {},
                  persist_audit=fake_persist_audit)
    assert rc == 0
    assert captured["audit"] is R.audit
    assert captured["cfg"] == {"enabled": True, "sink": "stdout"}


def test_main_builds_and_flushes_run_log():
    # CR-05: the entrypoint must construct a RunLog and pass it to the runner, then flush it.
    captured = {}

    class R:
        ok = True
        environments = {}

    def fake_runner(cfg, secrets, *, today, run_log=None, **kw):
        captured["run_log"] = run_log
        return R()

    rc = cli.main(["--config", "x.yaml", "--today", "2026-06-24"],
                  load_config=lambda p: {"run": {}, "log": {"sink": "stdout"}},
                  runner=fake_runner, load_secrets=lambda: {})
    assert rc == 0
    assert captured["run_log"] is not None
    assert hasattr(captured["run_log"], "flush")
