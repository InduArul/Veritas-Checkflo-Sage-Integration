"""Gate #2 (live) — delta (upsert+sweep) must reproduce the same table state as a full-refresh
(wipe+insert) for the same (env, window). specs/13 §13.12.

Contained: touches ONLY the Evergreen July GL slice (Source_Env='Evergreen', July). Other slices
and Veritashs are untouched. Leaves July in the correct (delta-produced == full-refresh) state.

Method (avoids the WHENMODIFIED-vs-BATCH_DATE filter difference by comparing against the SAME Sage
record set R):
  A) FULL-REFRESH: delete the EVG July GL slice, insert R, reconcile -> checksum A (the "truth").
  B) DELTA: perturb the slice (delete 5, corrupt 5, add 2 orphans), then upsert R + sweep July +
     reconcile -> checksum B.  Assert A == B.

Usage (VM): python parity_delta_vs_fullrefresh.py
"""
import os
from datetime import date
from intacct_etl import (config as cfgmod, db, secret_provider, sage_client, sage_transport,
                         retry, orchestrator, reconcile)
from intacct_etl.sql_loader import SqlLoader

JS, JE = date(2026, 7, 1), date(2026, 7, 31)
GL_COLS = ["Location_ID", "Location_Name", "Dept_ID", "Dept_Name", "Record_No", "Account_No",
           "Account_Title", "Entry_Date", "GL_Posting_Date", "Vendor_ID", "Vendor_Name", "Line_No",
           "Bill_No", "Base_Amount", "Description", "Memo", "Stampli_Link",
           "Platform_Services_Link", "Journal", "Source_Env"]
KEY = ["Record_No", "Line_No"]


def main():
    cfg = cfgmod.load_config("/data/intacct-etl/config.yaml")
    secrets = secret_provider.from_config(cfg.get("secrets"), env=dict(os.environ))
    sql = cfg["sql"]
    gl_opts, _ = orchestrator._query_opts(cfg)
    conn = db.connect(sql, secrets)
    cur = conn.cursor()
    loader = SqlLoader(conn, sql, source_env="Evergreen", require_test_targets=cfgmod.is_test_mode(cfg))

    env = {"name": "Evergreen", "sage_creds_key": "Prod_Sage_Evergreen", "enabled": True}
    creds = cfgmod.resolve_env_creds(cfg, env, secrets)
    tr = sage_transport.make_http_transport(creds.get("url") or cfg["sage"]["url"], timeout=60, gzip=True)
    client = sage_client.SageClient(tr, creds, retry=retry.from_config(cfg.get("retry", {})))
    sid = client.login()

    collist = ", ".join(f"[{c}]" for c in GL_COLS)

    def del_slice():
        cur.execute("DELETE FROM dbo.test_GLTable WHERE Source_Env='Evergreen' "
                    "AND GL_Posting_Date>=? AND GL_Posting_Date<=?", JS, JE)
        conn.commit()

    def slice_rows():
        cur.execute(f"SELECT {collist} FROM dbo.test_GLTable WHERE Source_Env='Evergreen' "
                    "AND GL_Posting_Date>=? AND GL_Posting_Date<=?", JS, JE)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    # R = the Sage truth for EVG July (BATCH_DATE window)
    R, err = client.fetch_gl(sid, JS, JE, page_size=2000, **gl_opts)
    print(f"Sage EVG July GL records R = {len(R)}  err={err}", flush=True)

    # ---- A) FULL-REFRESH: wipe slice + insert R + reconcile ----
    del_slice()
    loaded = loader.load_gl_records(R, page_size=2000, commit=True)
    loader.run_reconcile(JS, JE, commit=True)
    A_rows = slice_rows()
    A = reconcile.checksum(A_rows, KEY)
    print(f"A) full-refresh: loaded {loaded}, slice now {len(A_rows)} rows, checksum={A[:16]}", flush=True)

    # ---- perturb: simulate a stale/dirty SQL state that delta must repair ----
    victims = [(r["Record_No"], r["Line_No"]) for r in A_rows[:5]]
    for rn, ln in victims:
        cur.execute("DELETE FROM dbo.test_GLTable WHERE Source_Env='Evergreen' AND Record_No=? AND Line_No=?", rn, ln)
    for r in A_rows[5:10]:
        cur.execute("UPDATE dbo.test_GLTable SET Base_Amount='-999999' WHERE Source_Env='Evergreen' "
                    "AND Record_No=? AND Line_No=?", r["Record_No"], r["Line_No"])
    for i in (1, 2):
        cur.execute("INSERT INTO dbo.test_GLTable (Source_Env,Record_No,Line_No,Entry_Date,GL_Posting_Date) "
                    "VALUES ('Evergreen',?,?,?,?)", f"PARITY_ORPHAN_{i}", "1", date(2026,7,15), date(2026,7,15))
    conn.commit()
    print(f"perturbed: deleted 5, corrupted 5, added 2 orphans -> slice {len(slice_rows())} rows", flush=True)

    # ---- B) DELTA: upsert R + sweep July + reconcile (must converge back to A) ----
    loader.load_gl_records(R, page_size=2000, commit=True)                 # upsert: fix corrupted + re-add deleted
    present = {(str(r["RECORDNO"]), str(r["LINE_NO"])) for r in R}
    deleted = loader.sweep_gl(present, JS, JE, commit=True, max_delete_fraction=0.5)  # remove orphans
    loader.run_reconcile(JS, JE, commit=True)
    B_rows = slice_rows()
    B = reconcile.checksum(B_rows, KEY)
    print(f"B) delta: sweep removed {deleted}, slice now {len(B_rows)} rows, checksum={B[:16]}", flush=True)

    print("\n" + "=" * 60)
    print(f"GATE #2 (delta == full-refresh parity): {'PASS' if A == B else 'FAIL'}  "
          f"(A rows={len(A_rows)} B rows={len(B_rows)}; checksums {'MATCH' if A==B else 'DIFFER'})")
    if A != B:
        rec = reconcile.reconcile(A_rows, B_rows, KEY, sample=5)
        print("sample diffs:", rec.sample_diffs)
    conn.close()


if __name__ == "__main__":
    main()
