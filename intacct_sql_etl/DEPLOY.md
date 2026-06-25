# DEPLOY — Intacct → SQL Server ETL on Ubuntu

Deployment runbook for a **fresh Ubuntu 22.04/24.04 VM**. Commands are copy-paste ready.

> **Release gate (read first).** Per `CLAUDE.md`/`WAVES.md`, production cutover requires **green live
> parity vs Make** (W8) and is authorized explicitly. So you deploy in **two phases**:
> - **Phase 1 — Shadow run** (`run.test_mode: true`): writes only `test_`-prefixed tables, runs in
>   parallel with Make, and you reconcile. Safe to deploy now.
> - **Phase 2 — Cutover** (`run.test_mode: false`): only after parity sign-off. §10.
>
> The `prod_write_guard` is a dev-time guard; in production the **config** is the control — keep
> `test_mode: true` and `test_`-prefixed targets until cutover.

---

## 0. What gets installed

| Path | Purpose |
|---|---|
| `/opt/intacct-etl` | application + virtualenv (`venv/`) |
| `/etc/intacct-etl/config.yaml` | configuration (non-secret) |
| `/etc/intacct-etl/intacct-etl.env` | **secrets** (Sage + SQL), `chmod 600` |
| `/var/log/intacct-etl/` | run logs (`*run_log_*.txt`) |
| `/var/lib/intacct-etl/audit/` | audit trail (`audit_*.json` / `audit_trail.jsonl`) |
| `/etc/systemd/system/intacct-etl.{service,timer}` | scheduling |

---

## 1. Prerequisites

- Ubuntu VM with **outbound HTTPS** to Sage (`https://api.intacct.com`) and **TCP 1433** to SQL Server.
- The bundle `intacct_sql_etl-0.1.0.tar.gz` (provided).
- From the DBA (blocking — see `specs/10`): working **SQL credentials + auth mode** (OQ-11), the
  **`test_`-prefixed tables and proc variants** provisioned (OQ-1), and confirmation of the GL delete
  date field (OQ-2). From Sage: a Web Services user (sender + user creds) per environment.

```bash
# sanity
lsb_release -a
nc -vz api.intacct.com 443
nc -vz <SQL_SERVER_HOST> 1433
```

---

## 2. System packages (Python + Microsoft ODBC driver)

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip unixodbc curl gnupg ca-certificates

# Microsoft ODBC Driver 18 for SQL Server (Ubuntu 22.04 shown; use 24.04 in the URL on Noble)
curl -sSL https://packages.microsoft.com/keys/microsoft.asc | sudo tee /etc/apt/trusted.gpg.d/microsoft.asc >/dev/null
curl -sSL https://packages.microsoft.com/config/ubuntu/22.04/prod.list | sudo tee /etc/apt/sources.list.d/mssql-release.list >/dev/null
sudo apt-get update
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18

# verify the driver name (used in config.yaml -> sql.driver)
odbcinst -q -d
# expect: [ODBC Driver 18 for SQL Server]
```

---

## 3. Install the application

```bash
# upload the bundle to the VM, then:
mkdir -p ~/intacct-bundle && tar -xzf intacct_sql_etl-0.1.0.tar.gz -C ~/intacct-bundle
cd ~/intacct-bundle

# one-shot installer: creates the 'intacct' user, dirs, venv, installs the package + systemd units,
# and seeds /etc/intacct-etl/{config.yaml,intacct-etl.env} from templates (does NOT fill secrets)
sudo bash deploy/install.sh
```

Manual alternative (if you prefer not to use the script):
```bash
sudo useradd --system --home /opt/intacct-etl --shell /usr/sbin/nologin intacct
sudo install -d -o intacct -g intacct /opt/intacct-etl /var/lib/intacct-etl/audit /var/log/intacct-etl
sudo install -d -o root -g intacct -m 750 /etc/intacct-etl
sudo cp -r ~/intacct-bundle/. /opt/intacct-etl/ && sudo chown -R intacct:intacct /opt/intacct-etl
sudo python3 -m venv /opt/intacct-etl/venv
sudo /opt/intacct-etl/venv/bin/pip install --upgrade pip wheel
sudo /opt/intacct-etl/venv/bin/pip install /opt/intacct-etl
sudo cp ~/intacct-bundle/config/config.example.yaml /etc/intacct-etl/config.yaml
sudo cp ~/intacct-bundle/deploy/intacct-etl.env.example /etc/intacct-etl/intacct-etl.env
```

---

## 4. Configure (`/etc/intacct-etl/config.yaml`)

Edit the config — **no secrets here**. Key fields to set:

```bash
sudo nano /etc/intacct-etl/config.yaml
```

| Field | Set to |
|---|---|
| `run.test_mode` | **`true`** (Phase 1) |
| `environments[]` | one block per Sage company; `name` + `sage_creds_key` (the secret prefix) |
| `sage.url` | `https://api.intacct.com/ia/xml/xmlgw.phtml` |
| `sql.server` / `sql.database` | your SQL host\instance + DB |
| `sql.auth_mode` | `sql` (UID/PWD) \| `windows` \| `aad` (OQ-11) |
| `sql.username` | SQL login (if `auth_mode: sql`); password comes from the env file |
| `sql.driver` | **`ODBC Driver 18 for SQL Server`** (from §2) |
| `sql.odbc_extra` | `Encrypt=yes;TrustServerCertificate=yes` (driver 18 encrypts by default) |
| `sql.*_table` / `sql.proc_*` | **`test_`-prefixed** names for Phase 1 (e.g. `dbo.test_GLTable`, `test_AddGLDetailBulk`) |
| `sql.gl_delete_date_field` | confirm with DBA (OQ-2) |
| `log.dir` | `/var/log/intacct-etl` |
| `audit.dir` | `/var/lib/intacct-etl/audit` |

Then the **secrets** file (Sage + SQL), and lock it down:

```bash
sudo nano /etc/intacct-etl/intacct-etl.env     # fill from deploy/intacct-etl.env.example
sudo chown root:intacct /etc/intacct-etl/intacct-etl.env
sudo chmod 600 /etc/intacct-etl/intacct-etl.env
```

---

## 5. Verify the install (no data written)

```bash
RUN="sudo -u intacct /opt/intacct-etl/venv/bin/intacct-etl"

# CLI present
$RUN --help

# config loads + validates (and confirms test_ targets in test_mode)
sudo -u intacct /opt/intacct-etl/venv/bin/python -c \
 "from intacct_etl import config; c=config.load_config('/etc/intacct-etl/config.yaml'); \
  config.assert_test_targets(c); print('config OK; test_ targets OK')"

# (optional) run the test suite shipped in the bundle
cd /opt/intacct-etl && sudo -u intacct ./venv/bin/pip install pytest >/dev/null && \
  sudo -u intacct ./venv/bin/python -m pytest -q
# expect: 152 passed
```

---

## 6. First run — Phase 1 shadow (parallel to Make, `test_` tables)

```bash
RUN="sudo -u intacct /opt/intacct-etl/venv/bin/intacct-etl --config /etc/intacct-etl/config.yaml"

# normal daily run (window derived from today)
set -a; source /etc/intacct-etl/intacct-etl.env; set +a   # only needed for manual run
$RUN

# force a specific run type / a backfill window:
$RUN --run-type daily
$RUN --start 02/01/2026 --end 02/28/2026     # MM/DD/YYYY

# exit code: 0 = all environments OK, 1 = degraded (see logs/audit for which env)
echo "exit=$?"
```

---

## 7. Review the results

**Run log (human-readable):**
```bash
ls -t /var/log/intacct-etl/ | head
sudo tail -n 50 /var/log/intacct-etl/dailyrun_log_$(date +%m%d%Y).txt
```

**Audit trail (structured, for monitoring/analysis):**
```bash
# latest per-run JSON
ls -t /var/lib/intacct-etl/audit/*.json | head -1 | xargs sudo cat | python3 -m json.tool | head -60

# key numbers at a glance (read / loaded / rows_affected / failed + per-step seconds)
LATEST=$(ls -t /var/lib/intacct-etl/audit/*.json | head -1)
sudo python3 - "$LATEST" <<'PY'
import json,sys
a=json.load(open(sys.argv[1]))
t=a["totals"]
print(f"run {a['run_id']} {a['run_type']} {a['window']['start']}..{a['window']['end']} ok={a['ok']}")
print(f"TOTAL read={t['read']} loaded={t['loaded']} rows_affected={t['rows_affected']} failed={t['failed']}")
for e in a["environments"]:
    print(f"  {e['name']:<12} {e['status']:<8} read={e['total_read']:<7} loaded={e['total_loaded']:<7} "
          f"affected={e['total_affected']:<7} E={e['extract_s']:.2f}s T={e['transform_s']:.2f}s "
          f"L={e['load_s']:.2f}s wipe={e['wipe_s']:.2f}s recon={e['reconcile_s']:.2f}s dur={e['duration_s']:.2f}s")
    if e["error"]: print(f"     ERROR: {e['error']}")
PY
```

**Parity vs Make (the gate for cutover).** With Make still running into prod tables and the ETL into
`test_` tables for the same window, compare counts/values in SQL:
```sql
-- rowcounts
SELECT (SELECT COUNT(*) FROM dbo.GLTable)      AS make_gl,
       (SELECT COUNT(*) FROM dbo.test_GLTable) AS etl_gl,
       (SELECT COUNT(*) FROM dbo.APBillTable)      AS make_ap,
       (SELECT COUNT(*) FROM dbo.test_APBillTable) AS etl_ap;
-- spot-diff a window (adjust keys/cols to your schema)
SELECT TOP 50 * FROM dbo.GLTable      EXCEPT SELECT TOP 50 * FROM dbo.test_GLTable;
```
The repo's reconciliation harness (`tests/reconciliation/`, `intacct_etl.reconcile`) automates
row-count + checksum + sample diff once both sides are queryable.

---

## 8. Schedule (twice daily)

```bash
# set the VM timezone first (OQ-8 — confirm with the business)
sudo timedatectl set-timezone America/Los_Angeles

# Phase 1: you may run the timer to shadow Make continuously, OR run manually until parity passes.
sudo systemctl enable --now intacct-etl.timer
systemctl list-timers intacct-etl.timer          # next run times
sudo systemctl start intacct-etl.service          # trigger an immediate run
journalctl -u intacct-etl.service -n 50 --no-pager
```

---

## 9. Cutover to production (Phase 2 — only after parity sign-off + authorization)

```bash
sudo nano /etc/intacct-etl/config.yaml
#   run.test_mode: false
#   sql.gl_table:      dbo.GLTable
#   sql.apbill_table:  dbo.APBillTable
#   sql.proc_add_gl:   AddGLDetailBulk
#   sql.proc_add_ap:   AddAPBillBulk
#   sql.proc_sync_links:    [Intacct].[dbo].[SyncAPLinksToGL]
#   sql.proc_update_vendor: [Intacct].[dbo].[UpdateVendorInfoInGL]

# run once, verify, then DISABLE the Make scenario (keep it for rollback)
sudo systemctl start intacct-etl.service
journalctl -u intacct-etl.service -n 80 --no-pager
```
Rollback = re-enable Make + `systemctl disable --now intacct-etl.timer`. Make is untouched until you
disable it, so rollback is immediate.

---

## 10. Operations & troubleshooting

```bash
# logs
journalctl -u intacct-etl.service -f                 # live service log (stdlib logging)
sudo ls -t /var/log/intacct-etl/                     # run-log files
sudo ls -t /var/lib/intacct-etl/audit/               # audit records

# common issues
#  - "Data source name not found" / driver: check `odbcinst -q -d` and sql.driver in config.yaml
#  - login/timeout: check the .env secret keys match environments[].sage_creds_key + sql.password_secret_key
#  - SSL/Encrypt errors on driver 18: set sql.odbc_extra "Encrypt=yes;TrustServerCertificate=yes"
#  - degraded exit (1): open the latest audit JSON -> the failed env's "error"

# log rotation (optional)
printf '%s\n' '/var/log/intacct-etl/*.txt {' '  weekly' '  rotate 12' '  compress' '  missingok' '  notifempty' '}' \
 | sudo tee /etc/logrotate.d/intacct-etl >/dev/null
```

**Upgrades:** unpack the new bundle and `sudo /opt/intacct-etl/venv/bin/pip install --upgrade
~/intacct-bundle` (config + secrets in `/etc/intacct-etl` are preserved).
