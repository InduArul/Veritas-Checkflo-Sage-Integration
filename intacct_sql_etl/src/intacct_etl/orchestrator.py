"""Orchestration (specs/05). Config-driven; external factories injected for testing.

`run()` ties together: run_type/window derivation (+ overrides), month-chunk slicing, per-environment
loop with credential resolution, table wipe, GL+AP extract/load per chunk, reconcile (granularity
configurable), all inside a per-environment transaction (crash-safety), with failure isolation.
Nothing is hardcoded — table/proc names, page size, filters, strategy, granularity, query options
come from config.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime

from . import config as cfgmod
from . import dates
from .sage_client import SageError
from .sql_loader import SqlLoader

log = logging.getLogger(__name__)


# ---- W1 helper (used by the dry-run parity test) ----
def run_gl_for_window(creds, start: date, end: date, *, sage_client, loader,
                      page_size: int = 2000) -> dict:
    session_id = sage_client.login()
    records, gl_error = sage_client.fetch_gl(session_id, start, end, page_size=page_size)
    gl_loaded = loader.load_gl_records(records, page_size=page_size)
    return {"gl_records": len(records), "gl_loaded": gl_loaded, "gl_error": gl_error}


# ---- W5 full run ----
@dataclass
class RunResult:
    run_type: str
    start: date
    end: date
    environments: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(
            e.get("error") is None and e.get("gl_error") is None and e.get("ap_error") is None
            for e in self.environments.values()
        )


def _new_entry() -> dict:
    """Single source for the per-environment result schema (CR-14)."""
    return {
        "gl_records": 0, "gl_loaded": 0, "ap_records": 0, "ap_loaded": 0,
        "gl_error": None, "ap_error": None,        # scalar (first error) for back-compat
        "gl_errors": [], "ap_errors": [],          # full list (CR-13: no overwrite)
        "error": None, "rollback_error": None, "close_error": None,
    }


def _parse_mdy(s: str) -> date:
    return datetime.strptime(s, "%m/%d/%Y").date()


def _derive(today: date, run_cfg: dict) -> tuple[str, date, date]:
    wo = run_cfg.get("window_override")
    if wo:
        start, end = _parse_mdy(wo["start"]), _parse_mdy(wo["end"])
    else:
        start, end = dates.derive_window(today, run_cfg.get("daily_lookback_months", 3))
    rt_override = run_cfg.get("run_type_override")
    run_type = rt_override if rt_override and rt_override != "auto" else dates.derive_run_type(today)
    return run_type, start, end


def _query_opts(cfg: dict) -> tuple[dict, dict]:
    """Build GL/AP query options from config (CR-06: nothing hardcoded).

    Only includes keys that are actually configured, so the builders fall back to their
    documented defaults otherwise. AP `fields` are intentionally omitted — the client picks the
    probe (4-field) vs page (7-field) set itself.
    """
    gl, ap = cfg.get("gl", {}) or {}, cfg.get("ap", {}) or {}
    sage, run = cfg.get("sage", {}) or {}, cfg.get("run", {}) or {}
    common = {}
    if sage.get("dtd_version"):
        common["dtd_version"] = sage["dtd_version"]
    if run.get("date_format"):
        common["date_format"] = run["date_format"]

    gl_opts = dict(common)
    for src, dst in (("date_field", "date_field"), ("book_filter", "book_filter"),
                     ("select_fields", "fields"), ("order_by", "order_by")):
        if gl.get(src):
            gl_opts[dst] = gl[src]

    ap_opts = dict(common)
    for src, dst in (("date_field", "date_field"), ("order_by", "order_by")):
        if ap.get(src):
            ap_opts[dst] = ap[src]
    if "show_private" in ap:
        ap_opts["show_private"] = ap["show_private"]
    return gl_opts, ap_opts


def _process_environment(env, *, cfg, secrets, start, end, chunks, granularity, page_size,
                         require_test, strategy, gl_opts, ap_opts, connect,
                         sage_client_factory, settle_s, sleep, run_log) -> dict:
    """Run one environment end-to-end inside a single transaction (CR-12 extraction).

    Returns the result entry. Never raises — per-environment failures are isolated and recorded
    so one environment cannot corrupt another (specs/07 §7.5).
    """
    name = env["name"]
    entry = _new_entry()
    conn = None
    try:
        if strategy != "transaction":
            raise NotImplementedError(
                f"sql.load_strategy '{strategy}' not implemented (use 'transaction')"
            )
        creds = cfgmod.resolve_env_creds(cfg, env, secrets)
        conn = connect(cfg["sql"], secrets)
        loader = SqlLoader(conn, cfg["sql"], require_test_targets=require_test)
        client = sage_client_factory(creds)

        loader.wipe_window(start, end, commit=False)
        session_id = client.login()
        for cs, ce in chunks:
            gl, gl_err = client.fetch_gl(session_id, cs, ce, page_size=page_size, **gl_opts)
            if gl_err:
                entry["gl_errors"].append(gl_err)
                entry["gl_error"] = entry["gl_error"] or gl_err
                log.warning("%s: GL error in chunk %s..%s: %s", name, cs, ce, gl_err)
            entry["gl_records"] += len(gl)
            entry["gl_loaded"] += loader.load_gl_records(gl, page_size=page_size, commit=False)

            ap, ap_err = client.fetch_ap(session_id, cs, ce, page_size=page_size, **ap_opts)
            if ap_err:
                entry["ap_errors"].append(ap_err)
                entry["ap_error"] = entry["ap_error"] or ap_err
                log.warning("%s: AP error in chunk %s..%s: %s", name, cs, ce, ap_err)
            entry["ap_records"] += len(ap)
            entry["ap_loaded"] += loader.load_ap_records(ap, page_size=page_size, commit=False)

            if granularity == "per_chunk":
                if settle_s:
                    sleep(settle_s)
                loader.run_reconcile(cs, ce, commit=False)

        if granularity == "per_window":
            if settle_s:
                sleep(settle_s)
            loader.run_reconcile(start, end, commit=False)

        conn.commit()
        log.info("%s: complete (gl=%d, ap=%d)", name, entry["gl_loaded"], entry["ap_loaded"])
        if run_log:
            run_log.add(
                f'Processing for "{name}" complete: '
                f'GL {entry["gl_loaded"]} loaded, AP {entry["ap_loaded"]} loaded'
            )
    except Exception as exc:
        # Per-environment isolation: record, roll back, continue. Expected failures
        # (SageError/ConfigError/NotImplementedError) log a clean message; unexpected types log at
        # ERROR with a stack trace so genuine bugs are not silently downgraded (CR-17).
        _EXPECTED = (SageError, cfgmod.ConfigError, NotImplementedError)
        entry["error"] = str(exc) or exc.__class__.__name__
        unexpected = not isinstance(exc, _EXPECTED)
        log.error("%s: FAILED: %s", name, entry["error"], exc_info=unexpected)
        if conn is not None:
            try:
                conn.rollback()
            except Exception as rb:                 # CR-03: surface, never swallow
                entry["rollback_error"] = str(rb) or rb.__class__.__name__
                log.error("%s: ROLLBACK FAILED: %s", name, entry["rollback_error"])
        if run_log:
            run_log.add(f'Processing for "{name}" FAILED: {entry["error"]}')
    finally:
        if conn is not None:
            try:
                conn.close()                        # CR-01: deterministic close (no leak)
            except Exception as ce_err:
                entry["close_error"] = str(ce_err) or ce_err.__class__.__name__
                log.warning("%s: connection close failed: %s", name, entry["close_error"])
    return entry


def run(cfg: dict, secrets, *, today: date, sage_client_factory, connect,
        run_log=None, sleep=time.sleep) -> RunResult:
    run_cfg = cfg.get("run", {})
    run_type, start, end = _derive(today, run_cfg)
    chunks = dates.month_chunks(start, end)
    granularity = run_cfg.get("reconcile_granularity", "per_chunk")
    require_test = cfgmod.is_test_mode(cfg)
    page_size = cfg.get("sage", {}).get("page_size", 2000)
    strategy = cfg.get("sql", {}).get("load_strategy", "transaction")
    settle_s = cfg.get("retry", {}).get("reconcile_settle_s", 0) or 0
    gl_opts, ap_opts = _query_opts(cfg)

    result = RunResult(run_type=run_type, start=start, end=end)
    log.info("Start of process: run_type=%s window=%s..%s", run_type, start, end)
    if run_log:
        run_log.add("Start of process")
        run_log.add(f"Run type: {run_type}; window: {start} .. {end}")

    for env in cfg.get("environments", []):
        if not env.get("enabled", True):
            continue
        entry = _process_environment(
            env, cfg=cfg, secrets=secrets, start=start, end=end, chunks=chunks,
            granularity=granularity, page_size=page_size, require_test=require_test,
            strategy=strategy, gl_opts=gl_opts, ap_opts=ap_opts, connect=connect,
            sage_client_factory=sage_client_factory, settle_s=settle_s, sleep=sleep,
            run_log=run_log,
        )
        result.environments[env["name"]] = entry

    log.info("End of process: ok=%s", result.ok)
    return result
