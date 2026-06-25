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
