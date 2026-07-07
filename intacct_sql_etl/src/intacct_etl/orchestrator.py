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
from datetime import date, datetime, timedelta
from typing import Callable

from . import audit as auditmod
from . import config as cfgmod
from . import dates
from . import watermark as wmmod
from .sage_client import SageError
from .sql_loader import SqlLoader

DELTA_MODES = ("upsert", "sweep", "sweep-upsert")

log = logging.getLogger(__name__)


def _progress(run_log, msg: str) -> None:
    """Emit a per-chunk progress line to the logger (streamed live by run-etl.sh) AND the
    live-updating run log (OQ-34), so a long run is tail-able and its progress is visible."""
    log.info(msg)
    if run_log:
        run_log.add(msg)


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
    audit: object = None                            # audit.RunAudit (specs/12)

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


def _truncate_cutoff(now_dt: datetime) -> datetime:
    """Closed-time upper bound: drop sub-minute precision so we never page the open minute (§13.6)."""
    return now_dt.replace(second=0, microsecond=0)


def _effective_now(now, tz_name, injected: bool) -> datetime:
    """Wall-clock 'now' in the Sage company timezone (specs/13 §13.6; fixes the server-tz gap).

    If the caller injected a clock (tests) or no timezone is configured, use it as-is. Otherwise
    read the current time in the configured company tz so the WHENMODIFIED cutoff/watermark are on
    the same basis Sage filters (naive company-local), regardless of the VM's own timezone.
    """
    if injected or not tz_name:
        return now()
    try:
        from zoneinfo import ZoneInfo  # noqa: WPS433 (stdlib 3.9+)
        return datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
    except Exception:                   # bad tz name / no tzdata — fall back to the given clock
        return now()


def _delta_query_opts(cfg: dict):
    """Build (gl_delta, ap_delta, gl_keys, ap_keys) builder-option dicts from config (CR-06)."""
    gl, ap = cfg.get("gl", {}) or {}, cfg.get("ap", {}) or {}
    sage, run = cfg.get("sage", {}) or {}, cfg.get("run", {}) or {}
    common = {}
    if sage.get("dtd_version"):
        common["dtd_version"] = sage["dtd_version"]
    dtf, df = sage.get("datetime_format"), run.get("date_format")

    gl_delta = dict(common)
    if dtf:
        gl_delta["datetime_format"] = dtf
    for src, dst in (("book_filter", "book_filter"), ("modified_field", "modified_field"),
                     ("select_fields", "fields"), ("order_by", "order_by")):
        if gl.get(src):
            gl_delta[dst] = gl[src]

    ap_delta = dict(common)
    if dtf:
        ap_delta["datetime_format"] = dtf
    if ap.get("modified_field"):
        ap_delta["modified_field"] = ap["modified_field"]
    if "show_private" in ap:
        ap_delta["show_private"] = ap["show_private"]

    gl_keys = dict(common)
    if df:
        gl_keys["date_format"] = df
    if gl.get("book_filter"):
        gl_keys["book_filter"] = gl["book_filter"]
    ap_keys = dict(common)
    if df:
        ap_keys["date_format"] = df
    if "show_private" in ap:                 # sweep key pull MUST match the load's scope (gate #1)
        ap_keys["show_private"] = ap["show_private"]
    return gl_delta, ap_delta, gl_keys, ap_keys


def _gl_key_set(records):
    return {(str(r.get("RECORDNO", "")), str(r.get("LINE_NO", ""))) for r in records}


def _ap_key_set(records):
    return {(str(r.get("RECORDNO", "")),) for r in records}


def _process_environment_delta(env, *, do_upsert, do_sweep, cfg, secrets, start, end, cutoff,
                               grace, page_size, require_test, strategy, settle_s, sleep,
                               max_delete_fraction, chunk_months, opts, connect,
                               sage_client_factory, run_log, clock, now, commit_chunks=False):
    """Delta run for one environment (specs/13): watermark-driven upsert and/or delete-sweep,
    inside a single transaction. Watermark advances per object only on a clean fetch (§13.11).
    Never raises — failures isolated like the full-refresh path."""
    name = env["name"]
    entry = _new_entry()
    conn = None
    loader = None
    extract_s = 0.0
    started_at = now()
    t_start = clock()
    gl_delta_opts, ap_delta_opts, gl_keys_opts, ap_keys_opts = opts
    floor = datetime.combine(start, datetime.min.time())
    try:
        if strategy != "transaction":   # CR: match the full-refresh guard (fail loud on unsupported)
            raise NotImplementedError(
                f"sql.load_strategy '{strategy}' not implemented (use 'transaction')"
            )
        sql_cfg = cfg["sql"]            # CR: read config INSIDE try so a missing key isolates per-env
        wm_table = sql_cfg["watermark_table"]
        creds = cfgmod.resolve_env_creds(cfg, env, secrets)
        conn = connect(sql_cfg, secrets)
        loader = SqlLoader(conn, sql_cfg, source_env=name,
                           require_test_targets=require_test, clock=clock)
        client = sage_client_factory(creds)
        session_id = client.login()

        if do_upsert:
            # Chunk the WHENMODIFIED range (since, cutoff] into calendar-month sub-intervals and
            # process extract -> load -> release per chunk, so peak memory <= one chunk regardless
            # of how far behind the watermark is (specs/13 §13.16, NFR-MEM-1). GL and AP may have
            # different watermarks, so each gets its own chunk list.
            wm_gl = wmmod.get_watermark(conn, wm_table, name, "GL")
            since_gl = (wm_gl - grace) if wm_gl else floor
            for cs, ce in dates.group_chunks(dates.month_chunks_dt(since_gl, cutoff), chunk_months):
                _progress(run_log, f"{name} GL upsert {cs:%Y-%m-%d}..{ce:%Y-%m-%d}: fetching…")
                te = clock()
                gl, gl_err = client.fetch_gl_delta(session_id, cs, ce,
                                                   page_size=page_size, **gl_delta_opts)
                extract_s += clock() - te
                if gl_err:
                    entry["gl_errors"].append(gl_err)
                    entry["gl_error"] = entry["gl_error"] or gl_err
                    log.warning("%s: GL delta error in chunk %s..%s: %s", name, cs, ce, gl_err)
                entry["gl_records"] += len(gl)
                n = loader.load_gl_records(gl, page_size=page_size, commit=commit_chunks)
                entry["gl_loaded"] += n
                _progress(run_log, f"{name} GL upsert {cs:%Y-%m-%d}..{ce:%Y-%m-%d}: +{n}"
                                   + (" [committed]" if commit_chunks else ""))
                del gl                                              # release the chunk (§13.16)

            wm_ap = wmmod.get_watermark(conn, wm_table, name, "AP")
            since_ap = (wm_ap - grace) if wm_ap else floor
            for cs, ce in dates.group_chunks(dates.month_chunks_dt(since_ap, cutoff), chunk_months):
                _progress(run_log, f"{name} AP upsert {cs:%Y-%m-%d}..{ce:%Y-%m-%d}: fetching…")
                te = clock()
                ap, ap_err = client.fetch_ap_delta(session_id, cs, ce,
                                                   page_size=page_size, **ap_delta_opts)
                extract_s += clock() - te
                if ap_err:
                    entry["ap_errors"].append(ap_err)
                    entry["ap_error"] = entry["ap_error"] or ap_err
                    log.warning("%s: AP delta error in chunk %s..%s: %s", name, cs, ce, ap_err)
                entry["ap_records"] += len(ap)
                m = loader.load_ap_records(ap, page_size=page_size, commit=commit_chunks)
                entry["ap_loaded"] += m
                _progress(run_log, f"{name} AP upsert {cs:%Y-%m-%d}..{ce:%Y-%m-%d}: +{m}"
                                   + (" [committed]" if commit_chunks else ""))
                del ap                                              # release the chunk (§13.16)

            # Post-load reconcile (sync AP links -> GL, update vendor), same as the full-refresh path
            # (CR: was missing from delta -> broke parity, specs/13 §13.12). Once per window.
            if settle_s:
                sleep(settle_s)
            loader.run_reconcile(start, end, commit=False)

            # Completeness gate (§13.11): advance the watermark ONLY for an object whose EVERY chunk
            # fetched cleanly (any chunk error leaves entry[..._error] set and holds it back).
            if not entry["gl_error"]:
                wmmod.set_watermark(conn, wm_table, name, "GL", cutoff, commit=False, now=now())
            if not entry["ap_error"]:
                wmmod.set_watermark(conn, wm_table, name, "AP", cutoff, commit=False, now=now())

        if do_sweep:
            # Chunk the date window [start, end] into calendar-month sub-windows and sweep each in
            # turn (pull keys -> delete-missing -> release), so the ~600k-key nightly/monthly sweep
            # never materialises the whole window's key set at once (specs/13 §13.16, NFR-MEM-1).
            # Sub-windows partition [start, end], so per-chunk delete-missing == whole-window
            # delete-missing (parity, §13.12). AP sweep is windowed on a real date column (respects
            # accumulate, §13.9); with no such column configured it is SKIPPED — never a destructive
            # full-table compare that would delete historical bills Intacct no longer returns.
            ap_date_field = sql_cfg.get("apbill_sweep_date_field")
            if not ap_date_field:
                log.info("%s: AP sweep skipped — no apbill_sweep_date_field configured "
                         "(APBillTable needs a date column to sweep safely)", name)
            for cs, ce in dates.group_chunks(dates.month_chunks(start, end), chunk_months):
                gd0, ad0 = loader.metrics.gl_deleted, loader.metrics.ap_deleted
                _progress(run_log, f"{name} sweep {cs:%Y-%m-%d}..{ce:%Y-%m-%d}: pulling keys…")
                te = clock()
                gl_keys, gk_err = client.fetch_gl_keys(session_id, cs, ce,
                                                       page_size=page_size, **gl_keys_opts)
                extract_s += clock() - te
                if gk_err:
                    entry["gl_errors"].append(gk_err)
                    entry["gl_error"] = entry["gl_error"] or gk_err
                    log.warning("%s: GL keys error in chunk %s..%s — skipping its sweep "
                                "(never delete on partial): %s", name, cs, ce, gk_err)
                elif not gl_keys:
                    # Mass-delete guard: an empty (but error-free) key pull would mark every in-scope
                    # row 'missing'. Skip rather than nuke the sub-window.
                    log.warning("%s: GL key pull empty for chunk %s..%s — skipping its sweep "
                                "(mass-delete guard)", name, cs, ce)
                else:
                    loader.sweep_gl(_gl_key_set(gl_keys), cs, ce, commit=commit_chunks,
                                    max_delete_fraction=max_delete_fraction)
                del gl_keys                                         # release the chunk (§13.16)

                if not ap_date_field:
                    continue
                te = clock()
                ap_keys, ak_err = client.fetch_ap_keys(session_id, cs, ce,
                                                       page_size=page_size, **ap_keys_opts)
                extract_s += clock() - te
                if ak_err:
                    entry["ap_errors"].append(ak_err)
                    entry["ap_error"] = entry["ap_error"] or ak_err
                    log.warning("%s: AP keys error in chunk %s..%s — skipping its sweep: %s",
                                name, cs, ce, ak_err)
                elif not ap_keys:
                    log.warning("%s: AP key pull empty for chunk %s..%s — skipping its sweep "
                                "(mass-delete guard)", name, cs, ce)
                else:
                    loader.sweep_ap(_ap_key_set(ap_keys), cs, ce, date_field=ap_date_field,
                                    commit=commit_chunks, max_delete_fraction=max_delete_fraction)
                del ap_keys                                         # release the chunk (§13.16)
                _progress(run_log, f"{name} sweep {cs:%Y-%m-%d}..{ce:%Y-%m-%d}: "
                                   f"GL -{loader.metrics.gl_deleted - gd0} "
                                   f"AP -{loader.metrics.ap_deleted - ad0}")

        conn.commit()
        log.info("%s: delta complete (gl=%d ap=%d gl_del=%d ap_del=%d)", name,
                 entry["gl_loaded"], entry["ap_loaded"],
                 loader.metrics.gl_deleted, loader.metrics.ap_deleted)
        if run_log:
            run_log.add(
                f'Delta for "{name}" complete: GL {entry["gl_loaded"]} upserted, '
                f'AP {entry["ap_loaded"]} upserted, '
                f'{loader.metrics.gl_deleted} GL / {loader.metrics.ap_deleted} AP swept'
            )
    except Exception as exc:
        _EXPECTED = (SageError, cfgmod.ConfigError)
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
            run_log.add(f'Delta for "{name}" FAILED: {entry["error"]}')
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as ce_err:
                entry["close_error"] = str(ce_err) or ce_err.__class__.__name__
                log.warning("%s: connection close failed: %s", name, entry["close_error"])

    env_audit = _build_env_audit(name, entry, loader, extract_s=extract_s,
                                 started_at=started_at, finished_at=now(),
                                 duration_s=clock() - t_start)
    return entry, env_audit


def _process_environment(env, *, cfg, secrets, start, end, chunks, granularity, page_size,
                         require_test, strategy, gl_opts, ap_opts, connect,
                         sage_client_factory, settle_s, sleep, run_log,
                         clock, now) -> tuple[dict, "auditmod.EnvAudit"]:
    """Run one environment end-to-end inside a single transaction (CR-12 extraction).

    Returns (result entry, EnvAudit). Never raises — per-environment failures are isolated and
    recorded so one environment cannot corrupt another (specs/07 §7.5). Per-step timings/counts are
    captured for the audit trail (specs/12).
    """
    name = env["name"]
    entry = _new_entry()
    conn = None
    loader = None
    extract_s = 0.0
    started_at = now()
    t_start = clock()
    try:
        if strategy != "transaction":
            raise NotImplementedError(
                f"sql.load_strategy '{strategy}' not implemented (use 'transaction')"
            )
        creds = cfgmod.resolve_env_creds(cfg, env, secrets)
        conn = connect(cfg["sql"], secrets)
        loader = SqlLoader(conn, cfg["sql"], source_env=env["name"],
                           require_test_targets=require_test, clock=clock)
        client = sage_client_factory(creds)

        loader.wipe_window(start, end, commit=False)
        session_id = client.login()
        for cs, ce in chunks:
            te = clock()
            gl, gl_err = client.fetch_gl(session_id, cs, ce, page_size=page_size, **gl_opts)
            extract_s += clock() - te
            if gl_err:
                entry["gl_errors"].append(gl_err)
                entry["gl_error"] = entry["gl_error"] or gl_err
                log.warning("%s: GL error in chunk %s..%s: %s", name, cs, ce, gl_err)
            entry["gl_records"] += len(gl)
            n = loader.load_gl_records(gl, page_size=page_size, commit=False)
            entry["gl_loaded"] += n

            te = clock()
            ap, ap_err = client.fetch_ap(session_id, cs, ce, page_size=page_size, **ap_opts)
            extract_s += clock() - te
            if ap_err:
                entry["ap_errors"].append(ap_err)
                entry["ap_error"] = entry["ap_error"] or ap_err
                log.warning("%s: AP error in chunk %s..%s: %s", name, cs, ce, ap_err)
            entry["ap_records"] += len(ap)
            m = loader.load_ap_records(ap, page_size=page_size, commit=False)
            entry["ap_loaded"] += m
            _progress(run_log, f"{name} full-refresh {cs:%Y-%m-%d}..{ce:%Y-%m-%d}: GL +{n} AP +{m}")

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

    env_audit = _build_env_audit(name, entry, loader, extract_s=extract_s,
                                 started_at=started_at, finished_at=now(),
                                 duration_s=clock() - t_start)
    return entry, env_audit


def _build_env_audit(name, entry, loader, *, extract_s, started_at, finished_at,
                     duration_s) -> "auditmod.EnvAudit":
    """Assemble the per-environment audit record from the run entry + loader metrics (specs/12)."""
    committed = entry["error"] is None
    degraded = bool(entry["gl_errors"] or entry["ap_errors"])
    status = "failed" if not committed else ("degraded" if degraded else "ok")
    failed = (entry["gl_records"] + entry["ap_records"]) if not committed else 0

    gl_pages = getattr(loader.metrics, "gl_pages", 0) if loader else 0
    ap_pages = getattr(loader.metrics, "ap_pages", 0) if loader else 0
    gl_affected = getattr(loader.metrics, "gl_rows_affected", 0) if (loader and committed) else 0
    ap_affected = getattr(loader.metrics, "ap_rows_affected", 0) if (loader and committed) else 0
    gl_deleted = getattr(loader.metrics, "gl_deleted", 0) if (loader and committed) else 0
    ap_deleted = getattr(loader.metrics, "ap_deleted", 0) if (loader and committed) else 0
    # W8 observability: WHICH keys were swept + whether the rows_affected metric was captured.
    gl_deleted_keys = list(getattr(loader.metrics, "gl_deleted_keys", [])) if (loader and committed) else []
    ap_deleted_keys = list(getattr(loader.metrics, "ap_deleted_keys", [])) if (loader and committed) else []
    gl_affected_known = getattr(loader.metrics, "gl_rows_affected_known", True) if (loader and committed) else True
    ap_affected_known = getattr(loader.metrics, "ap_rows_affected_known", True) if (loader and committed) else True
    transform_s = (getattr(loader.metrics, "gl_encode_s", 0.0)
                   + getattr(loader.metrics, "ap_encode_s", 0.0)) if loader else 0.0
    load_s = (getattr(loader.metrics, "gl_db_s", 0.0)
              + getattr(loader.metrics, "ap_db_s", 0.0)) if loader else 0.0
    wipe_s = getattr(loader.metrics, "wipe_s", 0.0) if loader else 0.0
    reconcile_s = getattr(loader.metrics, "reconcile_s", 0.0) if loader else 0.0

    return auditmod.EnvAudit(
        name=name, status=status,
        gl_read=entry["gl_records"], ap_read=entry["ap_records"],
        gl_loaded=entry["gl_loaded"] if committed else 0,
        ap_loaded=entry["ap_loaded"] if committed else 0,
        gl_rows_affected=gl_affected, ap_rows_affected=ap_affected,
        gl_rows_affected_known=gl_affected_known, ap_rows_affected_known=ap_affected_known,
        gl_pages=gl_pages, ap_pages=ap_pages,
        gl_deleted=gl_deleted, ap_deleted=ap_deleted,
        gl_deleted_keys=gl_deleted_keys, ap_deleted_keys=ap_deleted_keys, failed=failed,
        extract_s=extract_s, transform_s=transform_s, load_s=load_s,
        wipe_s=wipe_s, reconcile_s=reconcile_s, duration_s=duration_s,
        started_at=started_at.isoformat() if started_at else None,
        finished_at=finished_at.isoformat() if finished_at else None,
        error=entry["error"], rollback_error=entry["rollback_error"],
        gl_errors=list(entry["gl_errors"]), ap_errors=list(entry["ap_errors"]),
    )


def run(cfg: dict, secrets, *, today: date, sage_client_factory, connect,
        run_log=None, sleep=time.sleep, clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = None) -> RunResult:
    now_injected = now is not None
    now = now or datetime.now
    run_cfg = cfg.get("run", {})
    run_type, start, end = _derive(today, run_cfg)
    chunks = dates.month_chunks(start, end)
    granularity = run_cfg.get("reconcile_granularity", "per_chunk")
    require_test = cfgmod.is_test_mode(cfg)
    page_size = cfg.get("sage", {}).get("page_size", 2000)
    strategy = cfg.get("sql", {}).get("load_strategy", "transaction")
    settle_s = cfg.get("retry", {}).get("reconcile_settle_s", 0) or 0
    gl_opts, ap_opts = _query_opts(cfg)
    mode = run_cfg.get("mode", "full")                         # specs/13: full | upsert | sweep | sweep-upsert
    delta = mode in DELTA_MODES
    cutoff = _truncate_cutoff(_effective_now(now, run_cfg.get("timezone"), now_injected))
    grace = timedelta(hours=cfg.get("sage", {}).get("watermark_grace_hours", 1))
    delta_opts = _delta_query_opts(cfg)
    max_delete_fraction = cfg.get("sql", {}).get("sweep_max_delete_fraction", 0.5)
    chunk_months = run_cfg.get("chunk_months", 1)              # specs/13 §13.16 bounded-memory chunking
    # OQ-32 commit granularity: daily = per_env (one atomic commit), monthly = per_chunk (resumable,
    # bounded t-log). Config `sql.commit_granularity` overrides the run_type-derived default.
    commit_gran = cfg.get("sql", {}).get("commit_granularity") \
        or ("per_chunk" if run_type == "monthly" else "per_env")
    commit_chunks = commit_gran == "per_chunk"

    started = now()
    t_run = clock()
    run_audit = auditmod.RunAudit(
        run_id=started.strftime("%Y%m%d_%H%M%S"), run_type=run_type,
        window_start=str(start), window_end=str(end), started_at=started.isoformat(),
    )
    result = RunResult(run_type=run_type, start=start, end=end, audit=run_audit)
    log.info("Start of process: mode=%s run_type=%s window=%s..%s", mode, run_type, start, end)
    if run_log:
        run_log.add("Start of process")
        run_log.add(f"Run type: {run_type}; window: {start} .. {end}")

    for env in cfg.get("environments", []):
        if not env.get("enabled", True):
            continue
        if delta:
            entry, env_audit = _process_environment_delta(
                env, do_upsert=mode in ("upsert", "sweep-upsert"),
                do_sweep=mode in ("sweep", "sweep-upsert"),
                cfg=cfg, secrets=secrets, start=start, end=end, cutoff=cutoff, grace=grace,
                page_size=page_size, require_test=require_test, strategy=strategy,
                settle_s=settle_s, sleep=sleep, max_delete_fraction=max_delete_fraction,
                chunk_months=chunk_months, opts=delta_opts, connect=connect,
                sage_client_factory=sage_client_factory, run_log=run_log, clock=clock, now=now,
                commit_chunks=commit_chunks,
            )
        else:
            entry, env_audit = _process_environment(
                env, cfg=cfg, secrets=secrets, start=start, end=end, chunks=chunks,
                granularity=granularity, page_size=page_size, require_test=require_test,
                strategy=strategy, gl_opts=gl_opts, ap_opts=ap_opts, connect=connect,
                sage_client_factory=sage_client_factory, settle_s=settle_s, sleep=sleep,
                run_log=run_log, clock=clock, now=now,
            )
        result.environments[env["name"]] = entry
        run_audit.environments.append(env_audit)

    run_audit.finished_at = now().isoformat()
    run_audit.duration_s = clock() - t_run
    if run_log:
        t = run_audit.totals()
        run_log.add(
            f"End of process: read={t['read']} loaded={t['loaded']} "
            f"failed={t['failed']} ok={result.ok}"
        )
    log.info("End of process: ok=%s totals=%s", result.ok, run_audit.totals())
    return result
