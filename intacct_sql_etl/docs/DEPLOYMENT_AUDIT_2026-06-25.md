# Deployment Audit Report — Intacct → SQL Server ETL

**Component:** `intacct_sql_etl` v0.1.0
**Host:** `VTSAzPLW01` (10.88.102.40) · Ubuntu 24.04.4 LTS · Python 3.12.3
**Install root:** `/data/intacct-etl/`
**Audit timestamp:** 2026-06-25T06:20Z
**Auditor:** Claude Code (automated deployment + verification over SSH)
**Phase:** 1 — Shadow (install only; live run gated on creds + parity)

---

## 1. Summary / verdict

| Area | Result |
|---|---|
| Installation | ✅ Complete (rootless, relocated to `/data`) |
| Functional verification | ✅ **152/152 tests pass on the host** |
| Integrity | ✅ Package + bundle checksummed |
| Security posture | ✅ No system changes; secrets `chmod 600`; no prod writes possible |
| Config readiness | ⚠️ **Action required** — SQL targets still prod-named; creds not filled |
| Live SQL prerequisite | ⏳ ODBC Driver 18 not installed (needs 1 sudo step) |
| Production cutover | ⛔ Gated on green parity vs Make (by design) |

**Bottom line:** the application is deployed, isolated, and proven working on the VM. It is **not yet
runnable against live systems** — three external dependencies remain (ODBC driver, credentials, and
DBA `test_` objects), all expected and tracked below.

---

## 2. Environment

- Host `VTSAzPLW01`, Ubuntu 24.04.4 LTS, kernel azure, uptime 14 days.
- Python 3.12.3; venv pip 26.1.2.
- Outbound to Sage (`api.intacct.com:443`) reachable.
- Disk `/data`: 125 G total, **106 G free** (12% used) — ample headroom; chosen to match the
  `stampli-automation` project convention (also on `/data`).
- Shared VM note: co-resident with the `stampli-automation` Docker stack (`/data/stampli-automation`).
  The ETL is fully isolated (own dir + venv); **no containers, ports, or system packages touched.**

---

## 3. What was deployed (`/data/intacct-etl/`)

| Path | Purpose | Notes |
|---|---|---|
| `venv/` | isolated Python env | 35 MB; rebuilt at this location |
| `venv/bin/intacct-etl` | CLI entry point | verified (`--help` OK) |
| `intacct_sql_etl-0.1.0/` | source bundle (code, specs, tests, docs) | — |
| `config.yaml` | active configuration | log/audit dirs rewritten to `/data/intacct-etl/...` |
| `intacct-etl.env` | secrets file | `-rw-------` (600), template (unfilled) |
| `logs/`, `audit/` | run outputs | empty (no run yet) |

**Install method:** rootless. System `python3-venv`/pip are absent and require root, so the venv was
created `--without-pip` and pip bootstrapped via `get-pip.py`. No `sudo`, no system-wide packages, no
files outside `/data/intacct-etl`.

### Dependencies installed (in venv)
```
pyodbc==5.3.0
requests==2.34.2
PyYAML==6.0.3
pytest==9.1.1
```

---

## 4. Integrity (sha256)

| Artifact | SHA-256 |
|---|---|
| `intacct_sql_etl-0.1.0.tar.gz` (bundle) | `b94198752dc0f4956a513b1ec0dfade51b55196bada2f6596bdd2d06907c7394` |
| installed `orchestrator.py` | `f1b1354dbd83ec7d14cab9c883fd826cb2a1ff61a84d36aa9f0f4096c64efdec` |
| installed `audit.py` | `e43b927e3af0edece8fd06e0a247259cb80ed75e1dfc3eb3b1b26bbfe749b175` |

Bundle was produced via `git archive` from branch `intacct-etl-code-review-gate` (tracked files only;
no secrets, caches, or build artifacts).

---

## 5. Functional verification

- **Test suite executed on the host:** `152 passed in 0.20s` (unit + integration + dry-run parity).
- CLI resolves and prints usage. Config loads. Package imports from `venv` site-packages.
- Code-review gate from the release: `docs/CODE_REVIEW.md` — **0 open findings**.

---

## 6. Security posture

| Control | Status |
|---|---|
| Secrets file permissions | ✅ `-rw-------` (600), owner `vtsazplw01-admin` |
| Secrets in source/config/logs | ✅ None (env-only; `config.yaml` holds no secrets) |
| System modifications | ✅ None (rootless; no apt, no system files) |
| Blast radius on shared VM | ✅ Contained to `/data/intacct-etl`; Docker stack untouched |
| Production write capability | ✅ Not possible — `test_mode: true` + config-level test_-target guard + `prod_write_guard` |
| Network exposure | ✅ None added (no listening service; CLI/cron only) |

---

## 7. Configuration state — ⚠️ action required before any run

`run.test_mode: true`, **but the SQL targets are still the shipped prod-named placeholders:**
```
gl_table: dbo.GLTable            apbill_table: dbo.APBillTable
proc_add_gl: AddGLDetailBulk     proc_add_ap: AddAPBillBulk
proc_sync_links: [Intacct].[dbo].[SyncAPLinksToGL]
proc_update_vendor: [Intacct].[dbo].[UpdateVendorInfoInGL]
```
In `test_mode`, the loader's guard will **refuse to run** until these are switched to `test_`-prefixed
names (e.g. `dbo.test_GLTable`, `test_AddGLDetailBulk`). This is correct fail-safe behavior, not a
defect — but it is a required edit before the first shadow run.

---

## 8. Outstanding items / gates (all expected)

> **Update 2026-06-30:** Item 1 (ODBC driver) is **DONE & verified**. On the host: unixODBC 2.3.12 +
> `[ODBC Driver 18 for SQL Server]` registered (`/opt/microsoft/msodbcsql18/lib64/libmsodbcsql-18.6.so.2.1`),
> and the app venv (`/data/intacct-etl/venv`, pyodbc 5.3.0) lists the driver. No install action was
> needed. **Remaining gates: config (`server: VPASQL01:1433`, `driver: ODBC Driver 18 for SQL Server`,
> `test_` targets, `Encrypt=...;TrustServerCertificate=yes`), secrets in `.env`, and confirm the `test_`
> stored-proc variants exist (tables already created).**

| # | Item | Owner | Blocks |
|---|---|---|---|
| 1 | ~~Install ODBC Driver 18~~ ✅ **DONE 2026-06-30** (Driver 18 + unixODBC present; pyodbc sees it) | — | — |
| 2 | Fill `config.yaml` (envs, SQL host/auth, **`test_` table/proc names**) | You + DBA | shadow run |
| 3 | Fill `intacct-etl.env` (Sage creds per env, `SQL_PASSWORD`) | You | shadow run |
| 4 | Provision `test_` tables + `test_` proc variants (OQ-1) | DBA | shadow run |
| 5 | Confirm GL delete date field (OQ-2) | DBA | correctness |
| 6 | Validate live Sage response shape (OQ-14) | first run | parity |
| 7 | Green parity vs Make → authorize cutover (`test_mode:false`) | You | production |
| 8 | Schedule (user crontab, rootless) | Claude/You | automation |

---

## 9. Sign-off

- **Deployment:** ✅ accepted — installed, isolated, verified (152 tests) at `/data/intacct-etl`.
- **Operational readiness:** ⏳ pending items 1–4 above (credentials + ODBC driver + DBA `test_` objects).
- **Production cutover:** ⛔ not authorized — requires green parity (WAVES W8 gate).

**Next action when creds arrive:** install ODBC driver (item 1), set `test_` targets + secrets (items
2–3), run the shadow ETL, then reconcile vs Make.
