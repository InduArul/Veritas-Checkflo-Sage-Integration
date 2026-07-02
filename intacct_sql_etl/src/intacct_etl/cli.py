"""CLI entrypoint (specs/05/06). All behavior config-driven; deps injectable for testing.

  python -m intacct_etl.cli --config config.yaml
  python -m intacct_etl.cli --config config.yaml --today 2026-06-24 --run-type monthly
  python -m intacct_etl.cli --config config.yaml --start 02/01/2026 --end 02/28/2026   # backfill
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime

from . import config as cfgmod
from . import (audit, dates, db, logging_setup, notify, orchestrator, retry, sage_client,
               sage_transport, secret_provider)

log = logging.getLogger(__name__)


def parse_args(argv):
    p = argparse.ArgumentParser(prog="intacct_etl")
    p.add_argument("--config", required=True, help="path to config.yaml")
    p.add_argument("--today", help="override run date (YYYY-MM-DD)")
    p.add_argument("--run-type", dest="run_type", choices=["auto", "daily", "monthly"])
    p.add_argument("--mode", choices=["full", "upsert", "sweep", "sweep-upsert"],
                   help="full = legacy full-refresh/seed; delta modes per specs/13")
    p.add_argument("--start", help="backfill window start (MM/DD/YYYY)")
    p.add_argument("--end", help="backfill window end (MM/DD/YYYY)")
    return p.parse_args(argv)


def _default_load_secrets():
    return dict(os.environ)


def _default_factory(cfg, secrets, retrier):
    sage_cfg = cfg.get("sage", {})

    def factory(creds):
        url = creds.get("url") or sage_cfg["url"]
        transport = sage_transport.make_http_transport(
            url, timeout=sage_cfg.get("request_timeout_s", 60), gzip=sage_cfg.get("gzip", True),
        )
        return sage_client.SageClient(transport, creds, retry=retrier)

    return factory


def _resolve_run_type(today: date, run_cfg: dict) -> str:
    rt = run_cfg.get("run_type_override")
    if not rt or rt == "auto":
        return dates.derive_run_type(today)
    return rt


def _default_run_log(cfg, run_type, when):
    return logging_setup.RunLog(cfg.get("log", {}), run_type=run_type, when=when)


def _default_make_notifier(cfg, secrets):
    return notify.from_config(cfg.get("notify"), secrets)


def main(argv=None, *, load_config=cfgmod.load_config, runner=orchestrator.run,
         load_secrets=None, sage_client_factory=None, connect=db.connect,
         make_run_log=_default_run_log, persist_audit=audit.write_audit,
         make_notifier=None):
    ns = parse_args(argv if argv is not None else sys.argv[1:])
    cfg = load_config(ns.config)
    secrets_raw = (load_secrets or _default_load_secrets)()
    # specs/14 Part A: resolve secrets through a provider (env now / Key Vault later; config-selected).
    secrets = secret_provider.from_config(cfg.get("secrets"), env=secrets_raw)
    try:                                                    # notifier setup is best-effort — never abort the run
        notifier = (make_notifier or _default_make_notifier)(cfg, secrets)
    except Exception as exc:
        log.error("notifier setup failed (continuing without notifications): %s", exc)
        notifier = None

    # CR-04: configure the standard logging framework (level from config).
    logging.basicConfig(
        level=cfg.get("log", {}).get("level", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    today = datetime.strptime(ns.today, "%Y-%m-%d").date() if ns.today else date.today()
    run_cfg = cfg.setdefault("run", {})
    if ns.run_type:
        run_cfg["run_type_override"] = ns.run_type
    if ns.mode:
        run_cfg["mode"] = ns.mode
    if ns.start and ns.end:
        run_cfg["window_override"] = {"start": ns.start, "end": ns.end}

    run_type = _resolve_run_type(today, run_cfg)
    run_log = make_run_log(cfg, run_type, today)            # CR-05: build + flush the run log
    retrier = retry.from_config(cfg.get("retry", {}))       # CR-18: config-driven retries

    # specs/14 §14.4: read-only pre-flight — fail fast + loud if SQL auth is broken (e.g. the
    # quarterly-rotated password expired) rather than running a silent broken job.
    if "sql" in cfg and cfg.get("run", {}).get("preflight", True):
        pf_err = db.preflight(cfg["sql"], secrets, connect=connect)
        if pf_err:
            log.error("pre-flight SQL check FAILED (password may have expired/rotated — update the "
                      "secret store): %s", pf_err)
            if notifier:
                notifier.alert(*notify.build_preflight_alert(pf_err))
            return 2

    factory = sage_client_factory or _default_factory(cfg, secrets, retrier)
    try:
        result = runner(cfg, secrets, today=today, sage_client_factory=factory,
                        connect=connect, run_log=run_log)
    finally:
        try:
            run_log.flush()
        except Exception as exc:                            # don't mask the run result
            log.error("failed to flush run log: %s", exc)

    # specs/12: persist the structured audit trail (never blocks the run)
    run_audit = getattr(result, "audit", None)
    if run_audit is not None:
        path = persist_audit(run_audit, cfg.get("audit", {}))
        if path:
            log.info("audit trail written: %s", path)

    # specs/14 Part B: email the daily report / failure alert (best-effort; never changes exit code).
    if notifier:
        notifier.notify(result)

    degraded = not getattr(result, "ok", True)
    for name, e in getattr(result, "environments", {}).items():
        status = "OK" if not (e.get("error") or e.get("gl_error") or e.get("ap_error")) else "DEGRADED"
        log.info("[%s] %s: %s", status, name, e)
    return 1 if degraded else 0


if __name__ == "__main__":
    raise SystemExit(main())
