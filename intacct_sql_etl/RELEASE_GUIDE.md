# Release Guide — Intacct → SQL Server ETL

**Release:** `intacct_sql_etl` **v0.1.0**
**Artifact:** `intacct_sql_etl-0.1.0.tar.gz`
**Target:** fresh Ubuntu 22.04 / 24.04 VM
**Status:** ✅ ready for **Phase-1 (shadow) deployment** · ⛔ production cutover gated on live parity

This is the top-level guide for releasing and operating the ETL. For the line-by-line Ubuntu
commands see **`DEPLOY.md`**; this guide gives the release context, the gate, the checklist, and the
go/no-go decisions around them.

---

## 1. What this release does

Replaces the Make.com "Intaact feeds to SQL" scenario family with a configurable Python ETL:
extracts GL + AP Bill data from Sage Intacct (XML Gateway), transforms it, and loads it into SQL
Server via the existing stored procedures (treated as black boxes). Runs on infrastructure you
already own at ~$0 incremental cost.

**What's included in the bundle**
- `src/intacct_etl/` — the package (extract → transform → load → reconcile → audit).
- `pyproject.toml` — installable package exposing the `intacct-etl` command.
- `DEPLOY.md` — detailed Ubuntu runbook. `RELEASE_GUIDE.md` — this file.
- `deploy/` — systemd `service` + twice-daily `timer`, secrets `EnvironmentFile` template, `install.sh`.
- `config/config.example.yaml` — configuration template (no secrets).
- `specs/` — the requirement specification (source of truth). `tests/` — 152 automated tests.

---

## 2. Release gate & known limitations (read before deploying)

Per `CLAUDE.md` / `WAVES.md`, correctness = **parity with Make**, and **no production write happens
until parity is signed off**. Therefore this release deploys in two phases:

| Phase | `run.test_mode` | Writes to | When |
|---|---|---|---|
| **1 — Shadow** | `true` | `test_`-prefixed tables only, in parallel with Make | **now** (this release) |
| **2 — Cutover** | `false` | production tables/procs | only after green parity + explicit sign-off |

**Quality gates already met for this release**
- ✅ Code review: `docs/CODE_REVIEW.md` — **0 open findings** (clean-code release gate).
- ✅ Tests: **152 passing** (unit + integration + dry-run parity), TDD throughout.
- ✅ Audit trail: structured per-run/per-step metrics (`specs/12`).

**Open items that block cutover (not Phase-1 shadow)** — from `specs/10`:
- OQ-11 — working SQL credentials + auth mode.
- OQ-1 — `test_`-prefixed tables + proc variants provisioned by the DBA.
- OQ-2 — confirm GL delete date field matches the extract window.
- OQ-14 — validate the live Sage response shape against the parsers (first real call).
- OQ-15 — insert-vs-update split needs proc support; until then the audit reports `rows_affected`.

---

## 3. Prerequisites

- Ubuntu VM with outbound **HTTPS → Sage** (`api.intacct.com:443`) and **TCP 1433 → SQL Server**.
- Microsoft **ODBC Driver 18 for SQL Server** (installed in DEPLOY.md §2).
- Sage Web Services credentials (sender + user) per environment; SQL login (or Windows/AAD).
- The bundle `intacct_sql_etl-0.1.0.tar.gz`.

---

## 4. Deploy in five moves (summary — full commands in DEPLOY.md)

```bash
# 1) unpack + install (creates user, venv, installs package + systemd units, seeds templates)
tar -xzf intacct_sql_etl-0.1.0.tar.gz && cd intacct_sql_etl-0.1.0
sudo bash deploy/install.sh

# 2) configure (NO secrets here)
sudo nano /etc/intacct-etl/config.yaml            # endpoints, SQL, test_ table/proc names, log/audit dirs

# 3) secrets (Sage + SQL), locked down
sudo nano /etc/intacct-etl/intacct-etl.env        # from deploy/intacct-etl.env.example
sudo chmod 600 /etc/intacct-etl/intacct-etl.env

# 4) verify (no data written)
sudo -u intacct /opt/intacct-etl/venv/bin/intacct-etl --config /etc/intacct-etl/config.yaml --help

# 5) first shadow run (test_mode=true)
set -a; source /etc/intacct-etl/intacct-etl.env; set +a
sudo -u intacct /opt/intacct-etl/venv/bin/intacct-etl --config /etc/intacct-etl/config.yaml
```

---

## 5. Configuration you must supply

**`/etc/intacct-etl/config.yaml`** (non-secret):
- `run.test_mode: true` (Phase 1)
- `environments[]` — one per Sage company: `name` + `sage_creds_key` (the secret prefix)
- `sage.url`
- `sql.server`, `sql.database`, `sql.auth_mode` (`sql`/`windows`/`aad`), `sql.username`,
  `sql.driver: ODBC Driver 18 for SQL Server`, `sql.odbc_extra: Encrypt=yes;TrustServerCertificate=yes`
- `sql.*_table` / `sql.proc_*` — **`test_`-prefixed** for Phase 1
- `sql.gl_delete_date_field` (OQ-2)
- `log.dir: /var/log/intacct-etl`, `audit.dir: /var/lib/intacct-etl/audit`

**`/etc/intacct-etl/intacct-etl.env`** (secrets, `chmod 600`): per-environment
`{sage_creds_key}_SENDER_ID/_SENDER_PASSWORD/_USER_ID/_USER_PASSWORD/_COMPANY_ID`, and `SQL_PASSWORD`.

---

## 6. Run

```bash
RUN="sudo -u intacct /opt/intacct-etl/venv/bin/intacct-etl --config /etc/intacct-etl/config.yaml"
$RUN                                  # daily window from today
$RUN --run-type monthly               # force monthly path
$RUN --start 02/01/2026 --end 02/28/2026   # backfill a window (MM/DD/YYYY)
# exit 0 = all environments OK; 1 = degraded (check audit/logs)
```

---

## 7. Review & monitor (the audit trail)

Each run emits a human run-log (`/var/log/intacct-etl/`) and a structured **audit record**
(`/var/lib/intacct-etl/audit/`) capturing, per environment and per object (GL/AP): rows **read**,
**loaded**, **rows_affected**, **failed**, and per-step durations (**Extract / Transform / Load** +
wipe/reconcile), plus timestamps and status — rolled up to run totals.

```bash
LATEST=$(ls -t /var/lib/intacct-etl/audit/*.json | head -1)
sudo python3 - "$LATEST" <<'PY'
import json,sys
a=json.load(open(sys.argv[1])); t=a["totals"]
print(f"run {a['run_id']} {a['run_type']} {a['window']['start']}..{a['window']['end']} ok={a['ok']}")
print(f"TOTAL read={t['read']} loaded={t['loaded']} rows_affected={t['rows_affected']} failed={t['failed']}")
for e in a["environments"]:
    print(f"  {e['name']:<12} {e['status']:<8} read={e['total_read']} loaded={e['total_loaded']} "
          f"affected={e['total_affected']} E={e['extract_s']:.2f} T={e['transform_s']:.2f} "
          f"L={e['load_s']:.2f} dur={e['duration_s']:.2f}s" + (f"  ERROR={e['error']}" if e['error'] else ""))
PY
```
Set `audit.format: jsonl` to append one record per run to `audit_trail.jsonl` for trending.

---

## 8. Parity check (go/no-go for cutover)

With Make writing prod tables and the ETL writing `test_` tables for the **same window**, compare:

```sql
SELECT (SELECT COUNT(*) FROM dbo.GLTable)      AS make_gl,
       (SELECT COUNT(*) FROM dbo.test_GLTable) AS etl_gl,
       (SELECT COUNT(*) FROM dbo.APBillTable)      AS make_ap,
       (SELECT COUNT(*) FROM dbo.test_APBillTable) AS etl_ap;
```
Parity = matching row counts + column checksums + clean sample diff (automated by
`intacct_etl.reconcile` / `tests/reconciliation/`). **Green parity is the cutover gate.**

---

## 9. Cutover (Phase 2 — after sign-off)

```bash
sudo nano /etc/intacct-etl/config.yaml
#   run.test_mode: false
#   sql.gl_table: dbo.GLTable ; sql.apbill_table: dbo.APBillTable
#   sql.proc_add_gl: AddGLDetailBulk ; sql.proc_add_ap: AddAPBillBulk
#   sql.proc_sync_links: [Intacct].[dbo].[SyncAPLinksToGL]
#   sql.proc_update_vendor: [Intacct].[dbo].[UpdateVendorInfoInGL]
sudo timedatectl set-timezone America/Los_Angeles   # OQ-8
sudo systemctl enable --now intacct-etl.timer
sudo systemctl start intacct-etl.service             # verify a live run
```
Then **disable the Make scenario** (keep it for rollback).

---

## 10. Rollback

Make stays live and untouched until you disable it, so rollback is immediate:
```bash
sudo systemctl disable --now intacct-etl.timer       # stop the Python ETL
# re-enable the Make scenario
```

---

## 11. Release verification checklist

- [ ] `odbcinst -q -d` shows the ODBC Driver 18.
- [ ] `intacct-etl --help` runs as the `intacct` user.
- [ ] `config.assert_test_targets` passes (Phase-1 `test_` targets).
- [ ] `pytest -q` → **152 passed** (optional on-VM).
- [ ] Shadow run completes; audit JSON shows expected read/loaded counts; `failed=0`.
- [ ] Parity vs Make green for the test window.
- [ ] (Cutover) `test_mode:false` + prod targets; one verified live run; Make disabled; rollback tested.

---

## 12. Support

- Runbook: `DEPLOY.md` · Spec: `specs/` · Config keys: `specs/06` · Audit: `specs/12`
- Open questions / decisions: `specs/10` · Code review: `docs/CODE_REVIEW.md`
- Logs: `journalctl -u intacct-etl.service` + `/var/log/intacct-etl/` · Audit: `/var/lib/intacct-etl/audit/`
