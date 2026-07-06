"""Gate #4 — FULL 19-column GL parity + Sage-authoritative count reconcile.

Extends the 2026-07-05 parity (which compared only 4 GL columns + key) to ALL 19 columns,
and adds a Sage-side count reconcile so we prove neither system dropped rows — not just that
we match Make's table.

Design (bounded memory — the 07-02 lesson): the full-column comparison is computed SERVER-SIDE
in SQL Server. We hash all 19 columns per row with SHA2_256 and join prod GLTable <-> test_GLTable
on the natural key (Record_No + Line_No). Nothing but small aggregates crosses the wire.

  PHASE A (DB, read-only):
    - total_test                 rows in test_GLTable
    - test_only_orphans          test keys absent from prod           (must be 0)
    - value_mismatch_19col       shared keys whose 19-col hash differs (must be 0)
    - prod_extra_lines           prod lines on a shared bill missing from test (must be 0)
    - up to 20 sample per-column diffs when value_mismatch_19col > 0
  PHASE B (Sage, count-only probes):
    - per calendar-month chunk, a pagesize=1 probe -> numremaining+1 (no data pull)
    - sum over the window vs test_GLTable count -> "neither system dropped rows"

Usage (on the VM, .env sourced):
  python gl_fullcol_parity.py 04/01/2026 07/31/2026
"""
import os
import sys
from datetime import datetime

from intacct_etl import (config as cfgmod, dates, db, pagination, retry,
                         sage_client, sage_query, sage_transport, secret_provider)
from intacct_etl import orchestrator

CFG = "/data/intacct-etl/config.yaml"
SENT = "N'∅'"          # NULL sentinel so NULL != '' in the canonical string
SEP = "NCHAR(31)"           # unit-separator delimiter (won't appear in data)


def mdy(s):
    return datetime.strptime(s, "%m/%d/%Y").date()


def gl_columns(cur):
    """Authoritative 19-col contract + ordinal order, straight from prod GLTable."""
    cur.execute(
        "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='GLTable' ORDER BY ORDINAL_POSITION")
    return [(r[0], r[1]) for r in cur.fetchall()]


def canonical_hash(alias, cols):
    """SHA2_256 over all columns of `alias`, deterministic & type-insensitive.

    Dates -> yyyy-mm-dd (style 23); everything else -> nvarchar; NULL -> sentinel.
    CAST to nvarchar(max) before HASHBYTES so no >4000-char truncation."""
    parts = []
    for name, dtype in cols:
        col = f"{alias}.[{name}]"
        expr = (f"CONVERT(varchar(10), {col}, 23)" if dtype in ("date", "datetime", "datetime2")
                else f"CAST({col} AS nvarchar(4000))")
        parts.append(f"COALESCE({expr}, {SENT})")
    concat = f"CONCAT({(',' + SEP + ',').join(parts)})"
    return f"HASHBYTES('SHA2_256', CAST({concat} AS nvarchar(max)))"


def phase_a(conn, cur):
    cols = gl_columns(cur)
    print(f"\n=== PHASE A — full {len(cols)}-column GL parity (server-side hash) ===", flush=True)
    print("cols: " + ", ".join(n for n, _ in cols), flush=True)

    th = canonical_hash("t", cols)
    ph = canonical_hash("p", cols)
    sql = f"""
    SELECT
      COUNT(*) AS total_test,
      SUM(CASE WHEN p.Record_No IS NULL THEN 1 ELSE 0 END) AS test_only_orphans,
      SUM(CASE WHEN p.Record_No IS NOT NULL AND {th} <> {ph} THEN 1 ELSE 0 END) AS value_mismatch_19col
    FROM dbo.test_GLTable t
    LEFT JOIN dbo.GLTable p ON p.Record_No = t.Record_No AND p.Line_No = t.Line_No"""
    cur.execute(sql)
    total, orphans, mismatch = cur.fetchone()

    cur.execute("""
    SELECT COUNT(*) FROM dbo.GLTable p
    WHERE EXISTS (SELECT 1 FROM dbo.test_GLTable t WHERE t.Record_No = p.Record_No)
      AND NOT EXISTS (SELECT 1 FROM dbo.test_GLTable t2
                      WHERE t2.Record_No = p.Record_No AND t2.Line_No = p.Line_No)""")
    prod_extra = cur.fetchone()[0]

    print(f"\n  total_test rows        : {total}", flush=True)
    print(f"  test_only_orphans      : {orphans}   (test keys not in prod; want 0)", flush=True)
    print(f"  value_mismatch_19col   : {mismatch}   (shared keys, any of 19 cols differ; want 0)", flush=True)
    print(f"  prod_extra_lines       : {prod_extra}   (prod lines on a shared bill missing from test; want 0)", flush=True)

    if mismatch:
        print("\n  --- sample diffs (up to 20 keys, per-column) ---", flush=True)
        cur.execute(f"""
        SELECT TOP 20 t.Record_No, t.Line_No
        FROM dbo.test_GLTable t
        JOIN dbo.GLTable p ON p.Record_No = t.Record_No AND p.Line_No = t.Line_No
        WHERE {th} <> {ph}
        ORDER BY t.Record_No, t.Line_No""")
        keys = cur.fetchall()
        collist = ", ".join(f"[{n}]" for n, _ in cols)
        for rec, line in keys:
            cur.execute(f"SELECT {collist} FROM dbo.test_GLTable WHERE Record_No=? AND Line_No=?", rec, line)
            trow = cur.fetchone()
            cur.execute(f"SELECT {collist} FROM dbo.GLTable WHERE Record_No=? AND Line_No=?", rec, line)
            prow = cur.fetchone()
            diffs = [f"{n}: test={tv!r} prod={pv!r}"
                     for (n, _), tv, pv in zip(cols, trow, prow) if str(tv) != str(pv)]
            print(f"    key=({rec},{line}): " + " | ".join(diffs), flush=True)

    passed = (orphans == 0 and mismatch == 0 and prod_extra == 0)
    print(f"\n  PHASE A: {'PASS' if passed else 'FAIL'}", flush=True)
    return passed, total


def phase_b(cfg, secrets, start, end, test_count):
    print(f"\n=== PHASE B — Sage-authoritative GL count vs test_GLTable ({start}..{end}) ===", flush=True)
    sage_cfg = cfg.get("sage", {})
    gl_opts, _ = orchestrator._query_opts(cfg)
    retrier = retry.from_config(cfg.get("retry", {}))
    env = next(e for e in cfg["environments"] if e.get("enabled", True))
    creds = cfgmod.resolve_env_creds(cfg, env, secrets)
    url = creds.get("url") or sage_cfg["url"]
    transport = sage_transport.make_http_transport(
        url, timeout=sage_cfg.get("request_timeout_s", 60), gzip=sage_cfg.get("gzip", True))
    client = sage_client.SageClient(transport, creds, retry=retrier)
    sid = client.login()

    chunks = dates.month_chunks(start, end)
    print(f"  env={env['name']}  chunks={len(chunks)}  gl_date_field={gl_opts.get('date_field')}", flush=True)
    sage_total = 0
    for cs, ce in chunks:
        probe = client._send(sage_query.build_gl_query_xml(
            session_id=sid, start=cs, end=ce, offset=0, page_size=1,
            return_json=False, creds=creds, **gl_opts))
        if probe.get("error"):
            print(f"    {cs}..{ce}: PROBE ERROR {probe['error']}", flush=True)
            continue
        n = pagination.gl_total(int(probe.get("numremaining") or 0))
        sage_total += n
        print(f"    {cs}..{ce}: sage={n}", flush=True)

    delta = test_count - sage_total
    print(f"\n  sage_authoritative_total : {sage_total}", flush=True)
    print(f"  test_GLTable_count       : {test_count}", flush=True)
    print(f"  delta (test - sage)      : {delta}   (want 0)", flush=True)
    passed = (delta == 0)
    print(f"\n  PHASE B: {'PASS' if passed else 'FAIL'}", flush=True)
    return passed


def main():
    start, end = mdy(sys.argv[1]), mdy(sys.argv[2])
    cfg = cfgmod.load_config(CFG)
    secrets = secret_provider.from_config(cfg.get("secrets"), env=dict(os.environ))
    conn = db.connect(cfg["sql"], secrets)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM dbo.test_GLTable")
    test_count = cur.fetchone()[0]
    cur.execute("SELECT DB_NAME()")
    print(f"DB={cur.fetchone()[0]}  test_GLTable={test_count}", flush=True)

    a_pass, total = phase_a(conn, cur)
    conn.close()
    b_pass = phase_b(cfg, secrets, start, end, test_count)

    print("\n" + "=" * 60, flush=True)
    print(f"GATE #4 (full-column + Sage reconcile): "
          f"{'PASS' if (a_pass and b_pass) else 'FAIL'}  "
          f"[A={'PASS' if a_pass else 'FAIL'} B={'PASS' if b_pass else 'FAIL'}]", flush=True)


if __name__ == "__main__":
    main()
