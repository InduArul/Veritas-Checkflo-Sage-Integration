"""Clean parallel-run baseline: wipe the drill-polluted test_ tables and reload BOTH entities
fresh for a defined window (Apr-Jul 2026), then seed watermarks. Gives a known Apr-Jul baseline
that mirrors Make's active window, so the daily reconcile vs Make is apples-to-apples.

Usage (VM): python reset_baseline.py 04/01/2026 07/31/2026
"""
import os, sys, time
from datetime import datetime
from intacct_etl import (config as cfgmod, dates, db, retry, sage_client, sage_transport,
                         secret_provider, orchestrator)
from intacct_etl.sql_loader import SqlLoader

ENVS = [("Evergreen", "Prod_Sage_Evergreen"), ("Veritashs", "Prod_Sage_Veritashs")]


def mdy(s): return datetime.strptime(s, "%m/%d/%Y").date()


def main():
    start, end = mdy(sys.argv[1]), mdy(sys.argv[2])
    cfg = cfgmod.load_config("/data/intacct-etl/config.yaml")
    secrets = secret_provider.from_config(cfg.get("secrets"), env=dict(os.environ))
    sql = cfg["sql"]; sage_cfg = cfg.get("sage", {}); ps = sage_cfg.get("page_size", 2000)
    gl_opts, ap_opts = orchestrator._query_opts(cfg)
    conn = db.connect(sql, secrets); cur = conn.cursor()

    print(f"RESET baseline window {start}..{end}", flush=True)
    cur.execute(f"DELETE FROM {sql['gl_table']}"); cur.execute(f"DELETE FROM {sql['apbill_table']}")
    conn.commit()
    cur.execute(f"SELECT COUNT(*) FROM {sql['gl_table']}"); g0 = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM {sql['apbill_table']}"); a0 = cur.fetchone()[0]
    print(f"  cleared: GL={g0} AP={a0}", flush=True)

    for name, key in ENVS:
        t0 = time.time()
        env = {"name": name, "sage_creds_key": key, "enabled": True}
        creds = cfgmod.resolve_env_creds(cfg, env, secrets)
        url = creds.get("url") or sage_cfg["url"]
        tr = sage_transport.make_http_transport(url, timeout=sage_cfg.get("request_timeout_s", 60),
                                                gzip=sage_cfg.get("gzip", True))
        client = sage_client.SageClient(tr, creds, retry=retry.from_config(cfg.get("retry", {})))
        loader = SqlLoader(conn, sql, source_env=name, require_test_targets=cfgmod.is_test_mode(cfg))
        sid = client.login()
        gl_tot = ap_tot = 0
        for cs, ce in dates.month_chunks(start, end):
            gl, ge = client.fetch_gl(sid, cs, ce, page_size=ps, **gl_opts)
            gl_tot += loader.load_gl_records(gl, page_size=ps, commit=False)
            ap, ae = client.fetch_ap(sid, cs, ce, page_size=ps, **ap_opts)
            ap_tot += loader.load_ap_records(ap, page_size=ps, commit=False)
            conn.commit()
            print(f"  {name} {cs}..{ce}: GL+{len(gl)} AP+{len(ap)}"
                  + (f" GLerr={ge}" if ge else "") + (f" APerr={ae}" if ae else ""), flush=True)
            del gl, ap
        loader.run_reconcile(start, end, commit=True)
        print(f"  {name} loaded: GL={gl_tot} AP={ap_tot} in {time.time()-t0:.0f}s", flush=True)

    # seed watermarks to end-of-window so future deltas catch changes after (no big catch-up)
    wm = datetime.combine(end, datetime.min.time())
    for name, _ in ENVS:
        for obj in ("GL", "AP"):
            cur.execute("IF EXISTS(SELECT 1 FROM dbo.test_etl_watermark WHERE environment=? AND object=?) "
                        "UPDATE dbo.test_etl_watermark SET watermark=?,updated_at=? WHERE environment=? AND object=? "
                        "ELSE INSERT INTO dbo.test_etl_watermark(environment,object,watermark,updated_at) VALUES(?,?,?,?)",
                        name, obj, wm, wm, name, obj, name, obj, wm, wm)
    conn.commit()
    cur.execute("SELECT Source_Env,COUNT(*) FROM dbo.test_GLTable GROUP BY Source_Env")
    print("FINAL GL:", dict(cur.fetchall()), flush=True)
    cur.execute("SELECT Source_Env,COUNT(*) FROM dbo.test_APBillTable GROUP BY Source_Env")
    print("FINAL AP:", dict(cur.fetchall()), flush=True)
    print(f"watermarks seeded to {wm}", flush=True)
    conn.close(); print("RESET COMPLETE", flush=True)


if __name__ == "__main__":
    main()
