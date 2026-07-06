"""Multi-entity ADDITIVE load — backfill a second environment's window into the shared
test_ tables WITHOUT deleting the entity already present.

Context (2026-07-06): the test_ tables held Evergreen only (the sole enabled env). The
"3,210 missing AP bills" are a second Sage company (Veritashs / 100-series). The normal
full-refresh path (wipe_window) would DELETE cross-entity (GL window delete + AP full wipe
are NOT entity-scoped) and clobber Evergreen. So this harness loads the second entity with
UPSERT ONLY (load_gl_records / load_ap_records) on the same BATCH_DATE/WHENPOSTED window
basis as the Evergreen full-refresh — NO wipe, NO sweep, NO delete. Purely additive.

Safe: writes ONLY test_ tables (require_test_targets). Idempotent if the bulk procs MERGE by
key (re-running a chunk won't duplicate). Per-chunk commit = progress survives a VPN drop.

Usage (VM, .env sourced):
  python additive_load_entity.py Veritashs Prod_Sage_Veritashs 04/01/2026 07/31/2026
"""
import os, sys, time
from datetime import datetime

from intacct_etl import (config as cfgmod, dates, db, retry, sage_client,
                         sage_transport, secret_provider, orchestrator)
from intacct_etl.sql_loader import SqlLoader

CFG = "/data/intacct-etl/config.yaml"


def mdy(s): return datetime.strptime(s, "%m/%d/%Y").date()


def counts(cur, gl_t, ap_t):
    cur.execute(f"SELECT COUNT(*) FROM {gl_t}"); g = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM {ap_t}"); a = cur.fetchone()[0]
    return g, a


def main():
    name, creds_key, start, end = sys.argv[1], sys.argv[2], mdy(sys.argv[3]), mdy(sys.argv[4])
    cfg = cfgmod.load_config(CFG)
    secrets = secret_provider.from_config(cfg.get("secrets"), env=dict(os.environ))
    sql = cfg["sql"]; sage_cfg = cfg.get("sage", {}); ps = sage_cfg.get("page_size", 2000)
    gl_opts, ap_opts = orchestrator._query_opts(cfg)
    env = {"name": name, "sage_creds_key": creds_key, "enabled": True}
    creds = cfgmod.resolve_env_creds(cfg, env, secrets)
    url = creds.get("url") or sage_cfg["url"]
    transport = sage_transport.make_http_transport(
        url, timeout=sage_cfg.get("request_timeout_s", 60), gzip=sage_cfg.get("gzip", True))
    client = sage_client.SageClient(transport, creds, retry=retry.from_config(cfg.get("retry", {})))

    conn = db.connect(sql, secrets)
    loader = SqlLoader(conn, sql, source_env=name, require_test_targets=cfgmod.is_test_mode(cfg))
    gl_t, ap_t = sql["gl_table"], sql["apbill_table"]
    cur = conn.cursor()

    g0, a0 = counts(cur, gl_t, ap_t)
    print(f"ENV={name}  window={start}..{end}  test_mode={cfgmod.is_test_mode(cfg)}", flush=True)
    print(f"BEFORE (Evergreen baseline): {gl_t}={g0}  {ap_t}={a0}", flush=True)
    print("MODE: ADDITIVE upsert only — NO wipe / NO sweep / NO delete\n", flush=True)

    sid = client.login()
    chunks = dates.month_chunks(start, end)
    tot_gl = tot_ap = 0
    for cs, ce in chunks:
        t0 = time.perf_counter()
        gl, gl_err = client.fetch_gl(sid, cs, ce, page_size=ps, **gl_opts)
        gl_loaded = loader.load_gl_records(gl, page_size=ps, commit=False)
        ap, ap_err = client.fetch_ap(sid, cs, ce, page_size=ps, **ap_opts)
        ap_loaded = loader.load_ap_records(ap, page_size=ps, commit=False)
        conn.commit()
        tot_gl += gl_loaded; tot_ap += ap_loaded
        gdb, adb = counts(cur, gl_t, ap_t)
        print(f"  {cs}..{ce}: +GL {gl_loaded} (+AP {ap_loaded})  ->  DB {gl_t}={gdb} {ap_t}={adb}  "
              f"{time.perf_counter()-t0:.1f}s"
              + (f"  GLerr={gl_err}" if gl_err else "") + (f"  APerr={ap_err}" if ap_err else ""), flush=True)
        del gl, ap

    print("\nrun_reconcile (stamp AP links + vendor into GL for the window) ...", flush=True)
    loader.run_reconcile(start, end, commit=True)

    g1, a1 = counts(cur, gl_t, ap_t)
    print(f"\nAFTER: {gl_t}={g1} (+{g1-g0})  {ap_t}={a1} (+{a1-a0})", flush=True)
    print(f"loaded this run: GL={tot_gl}  AP={tot_ap}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
