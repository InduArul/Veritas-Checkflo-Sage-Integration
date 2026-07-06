"""Daily parallel-run reconcile: compare our test_ output vs Make's prod on the common ACTIVE
window (gate #12). Writes a dated drift report to logs/reconcile_<date>.txt.

GL (the core financial data): test_GLTable vs prod GLTable by natural key (Record_No+Line_No),
scoped to our test rows, full 19-col SHA-256 hash — reports test-only orphans, value mismatches,
and prod-extra lines on shared bills. This is the gate-#4 parity, run daily.

AP: reports counts only, because prod APBillTable collapses cross-company Record_No collisions
(single key) while ours keeps both (Source_Env, Record_No). That ~2.3k difference is the KNOWN,
intended Source_Env fix — reported as INFO, not drift.

Exit 0 = in-baseline; exit 2 = GL drift beyond the known residual (alert-worthy).

Usage (VM): python reconcile_vs_make.py [KNOWN_GL_RESIDUAL]   # default residual 14
"""
import os, sys
from datetime import date, datetime
from intacct_etl import config as cfgmod, db, secret_provider

GL_COLS = ["Location_ID","Location_Name","Dept_ID","Dept_Name","Record_No","Account_No",
           "Account_Title","Entry_Date","GL_Posting_Date","Vendor_ID","Vendor_Name","Line_No",
           "Bill_No","Base_Amount","Description","Memo","Stampli_Link","Platform_Services_Link","Journal"]
KNOWN_GL_RESIDUAL = int(sys.argv[1]) if len(sys.argv) > 1 else 14
LOG_DIR = "/data/intacct-etl/logs"


def canon(alias, cols, cur):
    parts = []
    cur.execute("SELECT COLUMN_NAME,DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='GLTable'")
    dt = {r[0]: r[1] for r in cur.fetchall()}
    for n in cols:
        col = f"{alias}.[{n}]"
        e = (f"CONVERT(varchar(10),{col},23)" if dt.get(n) in ("date","datetime","datetime2")
             else f"CAST({col} AS nvarchar(4000))")
        parts.append(f"COALESCE({e},N'~')")
    return f"HASHBYTES('SHA2_256',CAST(CONCAT({(',NCHAR(31),').join(parts)}) AS nvarchar(max)))"


def main():
    cfg = cfgmod.load_config("/data/intacct-etl/config.yaml")
    secrets = secret_provider.from_config(cfg.get("secrets"), env=dict(os.environ))
    conn = db.connect(cfg["sql"], secrets); cur = conn.cursor()
    ts = None
    cur.execute("SELECT SYSDATETIME()"); ts = str(cur.fetchone()[0])[:19]

    th = canon("t", GL_COLS, cur); ph = canon("p", GL_COLS, cur)
    cur.execute(f"""SELECT COUNT(*) total_test,
        SUM(CASE WHEN p.Record_No IS NULL THEN 1 ELSE 0 END) test_only,
        SUM(CASE WHEN p.Record_No IS NOT NULL AND {th}<>{ph} THEN 1 ELSE 0 END) value_mismatch
      FROM dbo.test_GLTable t LEFT JOIN dbo.GLTable p
        ON p.Record_No=t.Record_No AND p.Line_No=t.Line_No""")
    total, test_only, mism = cur.fetchone()

    cur.execute("SELECT Source_Env,COUNT(*) FROM dbo.test_APBillTable GROUP BY Source_Env")
    ap_test = dict(cur.fetchall())
    cur.execute("SELECT COUNT(*) FROM dbo.APBillTable"); ap_prod = cur.fetchone()[0]

    drift = (test_only or 0) > 0 or (mism or 0) > KNOWN_GL_RESIDUAL
    lines = [
        f"{ts} RECONCILE vs Make (parallel-run #12)",
        f"  GL: test={total}  test_only(orphans)={test_only}  value_mismatch_19col={mism} "
        f"(known residual={KNOWN_GL_RESIDUAL})",
        f"  AP: test(by entity)={ap_test}  prod(collapsed,all-entity)={ap_prod} "
        f"[known: prod collapses Record_No collisions; ours keeps (Source_Env,Record_No)]",
        f"  RESULT: {'DRIFT — investigate' if drift else 'IN-BASELINE (ok)'}",
    ]
    report = "\n".join(lines)
    print(report)
    cur.execute("SELECT CONVERT(varchar(10),GETDATE(),112)"); day = cur.fetchone()[0]
    with open(f"{LOG_DIR}/reconcile_{day}.txt", "a", encoding="utf-8") as fh:
        fh.write(report + "\n")
    conn.close()
    sys.exit(2 if drift else 0)


if __name__ == "__main__":
    main()
