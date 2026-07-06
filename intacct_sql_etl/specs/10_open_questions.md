# 10 — Open Questions (to confirm before/while implementing)

Resolve these before coding the affected area. Owner in brackets.

- **OQ-1 [DBA/user] Stored-procedure source.** We must capture the bodies of `AddGLDetailBulk`,
  `AddAPBillBulk`, `SyncAPLinksToGL`, `UpdateVendorInfoInGL` (`SELECT OBJECT_DEFINITION(OBJECT_ID('dbo.<proc>'))`)
  into `reference/sql/`. They define the real JSON→column mapping, types, and dedup/merge. **Decision:**
  reuse the procs as-is (default), or port to Python? Until captured, the GL/AP column mappings are
  proc-defined, not in this spec.

- **OQ-2 [DBA/user] GL window date field. — RESOLVED 2026-07-02 (project owner):** use **`Entry_Date`**
  for the maintained window (matches the current project). Under the delta CR (`specs/13`) the upsert
  filters on `WHENMODIFIED` (change field), and the **delete-sweep window is defined on `Entry_Date`**
  on both the Intacct and SQL sides.

- **OQ-3 [DBA] Unicode literal asymmetry.** GL uses `N'…'` (NVARCHAR) but AP uses `'…'` for the `@json`
  literal. Incidental or meaningful (non-ASCII vendor names/descriptions)? Rebuild will bind Unicode-safe
  regardless — confirm no proc dependency.

- **OQ-4 [user] Reconcile granularity.** Today `SyncAPLinksToGL`/`UpdateVendorInfoInGL` run **per chunk**
  (per month window) with that chunk's dates. Keep per-chunk, or run once over the full window at the end?
  (Per-chunk is current behaviour = default for parity.)

- **OQ-5 [user/DBA] Load strategy.** Choose `transaction` (single tran per env/window) vs `staging_swap`
  (load to staging, atomic swap). Affects locking on the large GL table during the monthly full reload.

- **OQ-6 [user] SharePoint log upload.** Keep uploading the run-log `.txt` to
  `/Veritas Integrations/Intaact feeds to SQL Server/Logs` (module 119), or is a local/DB log + alerting
  sufficient? (Default off; enable via `log.sharepoint_upload`.)

- **OQ-7 [user] Session reuse scope.** Make logs in once **per environment**. Acceptable to keep; could
  one login serve the whole run if same company? (Minor; default = per-env, matches Make.)

- **OQ-8 [user] Schedule timezone + weekdays.** Exact cron, timezone, and active days for the 08:00 /
  17:30 runs aren't in the blueprint. Confirm from Make scheduling settings (the check-pipeline Part1
  used MoWeSa / TuThFr splits — does the SQL feed run all 7 days?).

- **OQ-9 [user] Alerting/DLQ.** What channel for degraded-run alerts (email/monitoring)? Replaces Make's
  `dlq` + the silent-overage gap. Where to persist failed windows for replay?

- **OQ-10 [user] Hosting/runtime.** Where does the Python run — the SQL Server box (RDP, per POC), a
  scheduled task, or a container? Drives the scheduling + secret-store mechanism. (POC ran PowerShell on
  the RDP SQL box; Python likely there too.)

- **OQ-11 [DBA] SQL credentials & auth mode.** The POC was blocked on SQL login (Memory_Opti §8): Azure
  SQL + AAD vs on-prem + SQL auth unknown; Make's stored creds are redacted. Need working creds + auth
  mode before any load test (`sql.auth_mode`, `sql.username/password` or AAD token).
  - **Partially resolved 2026-06-30 (IT):** Target SQL Server = **`VPASQL01`**, **standard port 1433**.
    IT verified from the Ubuntu VM (`VTSAzPLW01`) that the TCP port to VPASQL01 **accepts traffic** — the
    network path is open. (The separate PostgreSQL Flexible Server on 5432 is unrelated — ignore.)
    **Still outstanding:** (a) a working **SQL login** (SQL-auth user+password, or AAD/managed-identity),
    (b) confirm VPASQL01 is a default instance on 1433 (not named) + FQDN/IP, (c) ODBC Driver 18 install
    on the VM (sudo). Until (a) lands, no live connect possible despite the open port.
  - **2026-06-30 live connection test — FAILED (login timeout). Two concrete blockers found:**
    1. **Name resolution:** `VPASQL01` does **not resolve** from the VM (no DNS entry; resolv.conf search
       domain is Azure `reddog.microsoft.com`; no `/etc/hosts` entry). `getaddrinfo: Temporary failure in
       name resolution` for `VPASQL01`, `.veritas.local`, `.veritashs.com`. IT's port test must have used
       an IP / been run server-side — by name from this VM it cannot connect.
    2. **Named instance:** config server is `VPASQL01\SQLEXPRESS` — a **named** SQL Express instance, which
       needs **SQL Browser (UDP 1434)** or a **static TCP port**. Plain TCP 1433 reachability does not prove it.
    - **Fix / ask IT:** provide VPASQL01's **IP** + the **static TCP port** SQLEXPRESS listens on (or confirm
      UDP 1434 open). Then on Linux prefer `sql.server: "<IP>,<port>"` (host,port form — bypasses DNS *and*
      SQL Browser), optionally add `<IP> VPASQL01` to `/etc/hosts`. Config/driver/secret are otherwise validated OK.
    - **Note:** `test_` **stored-proc** existence still unverified (the connection test that checks them
      couldn't reach the DB). Re-run that check once the host is reachable.
  - **2026-06-30 (IP supplied: `40.75.111.131:1433`) — still FAILS: network path blocked.** Switched
    `sql.server` to the host,port form `"40.75.111.131,1433"` (bypasses DNS + SQL Browser). Findings:
    - VM private IP `10.88.102.40`; **VM public egress IP `135.224.198.106`** (so `40.75.111.131` is *not*
      the VM itself — it is a genuinely separate host). Outbound **443 to Sage works** (egress is fine).
    - **TCP `40.75.111.131:1433` and `:1434` both TIME OUT** (packets silently dropped → firewall/NSG,
      not a closed port). Route is correct (`via 10.88.102.1 dev eth0`).
    - **Diagnosis = firewall/NSG block on the SQL side**, not an app problem. App side is fully ready
      (driver/config/secret/validation all green). **Ask IT:** preferably give the SQL server's **private
      IP** and open its NSG + Windows firewall for inbound TCP 1433 from `10.88.102.40` (intra-VNet);
      otherwise open inbound 1433 on `40.75.111.131` from egress IP `135.224.198.106`. Confirm SQLEXPRESS
      listens on **static** TCP 1433. (IT's earlier "port accepting traffic" check was not from this VM's path.)

- **OQ-12 [user] Env source of truth.** Today environments come from `Config.xlsx`/`Env_data`. Move to
  the YAML `environments[]` (default), or keep reading the Excel file? List the current env names (sample
  shows `Evergreen`; sample length = 2).

- **OQ-14 [Sage/dev] Live response wire shape. — RESOLVED 2026-06-25 (validated live).**
  Confirmed against real Sage (env Evergreen): the **probe** (pagesize 1, no `returnformat`) returns
  **XML** with `data` attributes `numremaining`/`totalcount`; the **page** (`returnformat=json`)
  returns **`Content-Type: application/json` with a top-level JSON array** of flat record dicts
  (JSON `null` → `None`). `make_http_transport` now routes by request type + response content-type
  (`parse_query_text` → `parse_json_query_response` for JSON pages, `xml_to_dict`+`parse_query_response`
  for the XML probe). Login remains XML. Covered by unit tests in `tests/unit/test_sage_transport.py`.

- **OQ-15 [DBA] Insert vs update split for the audit trail.** The bulk procs
  (`AddGLDetailBulk`/`AddAPBillBulk`) merge via OPENJSON and return only a total `rowcount` (if any).
  The audit trail records `rows_affected` (total touched). A true inserted-vs-updated breakdown
  (`specs/12` FR-AUD-5) requires the proc to OUTPUT counts or return a result set. Confirm whether the
  procs can expose this; until then `rows_affected` is the monitored metric.

- **OQ-13 [DBA] GL count `numremaining+1`.** Confirm this correctly equals total rows in all cases (it
  assumes the pagesize-1 probe returns 1 row + N remaining). Edge case: zero-row windows → numremaining=0
  → total=1 but no data; the `length(data)>0` guard handles it, but verify the page loop count.

- **OQ-16 [user/DBA] `WHENMODIFIED` timezone** (delta CR, `specs/13`). **RESOLVED 2026-07-02: company timezone.**
  (OQ-16b: confirm exact datetime string format at build time for a boundary-safe round-trip.)

- **OQ-17 [user] Delta schedule** (`specs/13`). Confirm 08:00 / 17:30 upserts + 01:00 nightly (sweep+upsert)
  + 1st-of-month monthly (wide sweep+upsert); timezone/active weekdays (see also OQ-8).

- **OQ-18 [user] Retention purge** (`specs/13`; **user action item**). Decide if/when to purge history older
  than the window. Tables accumulate for now; purge built as an OFF-by-default toggle until confirmed.

- **OQ-19 [user] Seed depth** (`specs/13`). **RESOLVED 2026-07-02:** prod already has data from 2024-01-01
  (no prod seed); seed **`test_`** tables from 2024-01-01 → now to mirror live for parity.

- **OQ-20 [DBA] GL/AP hard-deletes. — RESOLVED 2026-07-02 (client).** Governed by the accounting period:
  **OPEN period → hard delete** (record disappears); **CLOSED period → reversal only** (original stays, new
  reversing entry added; some AP cases are reversal-only regardless). ⇒ Hard deletes occur ONLY in open
  (recent) periods; closed/old periods are never hard-deleted, only reversed (reversals = new rows caught by
  the delta upsert). IMPLICATION: the delete-sweep only needs to cover the **open period(s)**; the current
  GL windows (daily ~4-mo / monthly wide) comfortably cover them, and accumulate of old data is safe (old
  data is never deleted in Intacct). ASSUMPTION to watch: the sweep window must always span all currently-open
  periods — fine unless a period stays open longer than the daily window (~3-4 months).

- **OQ-21 [user] Delta `grace` interval** (`specs/13`). **RESOLVED 2026-07-02: 1 hour.**

- **OQ-26 [DBA] AP sweep date column. — APPROVED 2026-07-02 (client).** DBA will add a date column to the
  AP bill table (we recommend `When_Posted DATE`, populated from `WHENPOSTED`) + update `test_AddAPBillBulk`
  to map it. Then set `sql.apbill_sweep_date_field: When_Posted` → windowed AP sweep turns on (safe, like GL).
  Client also **explicitly reconfirmed accumulate: old data must NOT be deleted, stays in SQL** — our
  windowed sweep already honours this. Until the column lands, AP delete-sweep stays SKIPPED (AP upsert
  works; only AP *deletion* detection deferred). Code prep: add `WHENPOSTED` to the AP select set so the
  payload carries it (harmless to the current proc; OPENJSON ignores unmapped keys).

- **OQ-27 [IT] Mailbox send method** (`specs/14`; = C5). Graph `sendMail` (app registration) vs basic SMTP
  AUTH for `timeclockplus@veritashcsolutions.com` — sets `notify.transport`.

- **OQ-28 [IT] SharePoint read access** (`specs/14`; = C6). Managed identity / app-registration scope to read
  `Config.xlsx` (Sheet1/Table1). Until granted, `recipient_source: config`.

- **OQ-29 [user] Report cadence/branding** (`specs/14`). One daily success email (default) vs per-run; any
  subject convention/logo. Default: one email per run, plain HTML.

- **OQ-30 [user/DBA] Pre-flight strictness** (`specs/14`). On SQL auth failure: abort whole run + alert
  (default, fail-fast) vs continue and fail per-env.

- **OQ-33 [user] Bounded parallel monthly run. — PLANNED 2026-07-06 (enhancement, post-cutover).**
  The delta engine is sequential (one month-chunk at a time = bounded memory, the 07-02 fix). Since
  ~94% of time is Sage extraction (I/O-wait), a **bounded** worker pool (e.g. 4) could overlap the
  waiting and cut monthly wall-clock ~4x (Make gets ~20min by running ~20 chunks in parallel). Apply
  to the **monthly only**; keep daily sequential (already fast). MUST cap concurrency (4-6) — NOT all
  ~26 chunks at once (that reinvites the 07-02 swapless OOM: 4×~250MB≈1GB safe on 16GB; 26×≈6GB+ risk).
  Needs: thread pool (I/O-bound → threads release GIL), **one DB connection per worker**, deadlock
  retry on concurrent MERGE, per-chunk error isolation, config knob `monthly_parallelism`. **Initial
  seed stays SEQUENTIAL** (user, 2026-07-06). Not a go-live blocker — monthly can run overnight
  sequentially at first; parallelize as a fast-follow. NOTE: our monthly is already far lighter than
  Make's — the UPSERT pulls only WHENMODIFIED-since-watermark (not a full re-extract), and the wide
  sweep pulls keys-only; only the one-time initial seed is heavy.

- **OQ-2 date-basis DECISION 2026-07-06 (user): KEEP the two-date GL design** (extract on BATCH_DATE,
  delete/sweep on Entry_Date) — matches Make. Considered switching to one-date (delete on
  GL_Posting_Date=posting date, like AP already does) to remove the rare straddle edge case (a record
  whose posting-month != books-month could be deleted-not-reloaded). Data shows 0% straddle + 0 NULLs,
  so it's purely theoretical here. User decided: **leave it, don't touch it** — accept the rare edge
  case, keep Make parity. No change. (AP remains one-date on WHENPOSTED, which is fine.)

- **OQ-32 [user] Commit granularity for heavy loads. — APPROVED 2026-07-06 (user).** The delta engine
  is already chunked by calendar month (bounded memory) and pages the SQL proc at 2000 rows, but it
  **commits once per env** (whole run = one transaction). For the **monthly** wide run (~26 chunks)
  that is a huge long-lived transaction (t-log growth, long locks, full rollback+redo on a late
  failure). Decision: add `sql.commit_granularity` = **`per_env`** (default; atomic, crash-safe — used
  for the small **daily** delta) or **`per_chunk`** (commit after each month-chunk load + each sweep
  chunk — bounded t-log, **resumable**; used for the heavy **monthly** run). Default is derived from
  run_type (daily→per_env, monthly→per_chunk) unless overridden in config. Safe because the load is
  idempotent MERGE-upsert + windowed/entity-scoped sweep + the watermark only advances on a clean
  full pass (a mid-run failure just re-runs and converges). Trade-off accepted: per_chunk is NOT
  whole-run atomic (partial months visible mid-run) — fine for a big background monthly, which is why
  the daily stays per_env. Applies to the delta path only; the full-refresh seed (wipe-based) stays
  atomic per_env regardless.

- **OQ-31 [user/DBA] Multi-entity keying = `Source_Env`. — DIRECTION APPROVED 2026-07-06 (user).**
  `GLTable`/`APBillTable` are shared across all Sage companies with no company discriminator, and AP
  `Record_No` (Sage per-company PK) **collides across companies** (Evergreen vs Veritashs) → MERGE-by-
  `Record_No` silently overwrites (2,341 in the Apr–Jul test) and every delete/sweep is entity-blind
  (one env's run would wipe another's rows). **Decision:** add a **`Source_Env`** column; AP PK =
  **`(Source_Env, Record_No)`**; GL keeps `(Record_No, Line_No)` + `Source_Env` for scoping; all
  wipe/sweep gain `AND Source_Env = ?`; `SyncAPLinksToGL` join adds `Source_Env` (its `Record_ID` join
  is non-unique). Design: `docs/design/multi_entity_source_env_design.md`. Verified `RECORDNO` is unique
  *within* each company (Evergreen 20,834 / Veritashs 2,204, 0 dups); `Record_ID` is NOT (rejected).
  **Accepted divergence from Make:** AP row count ~27,784 vs prod's collision-collapsed 25,443.
  **Open sub-questions (for cutover — build starts in `test_` only):** (a) DBA owns the same column +
  merge-key + join change on **prod** procs/tables + change window; (b) which downstream consumers read
  these tables and can they handle the new column + AP count increase; (c) enumerate **all** Sage
  companies Make loads (only Evergreen + Veritashs enabled now); (d) prod `Source_Env` back-fill vs
  forward-only from cutover.

- **OQ-22 [user/IT] Secret store = Azure Key Vault + VM Managed Identity** (`specs/14`). **DECISION 2026-07-02:
  do NOT block the build on the vault.** Build behind a swappable `SecretProvider` interface; default impl =
  env/`.env` (chmod 600, as today); **repoint to Key Vault later** with no code change. When IT is ready:
  create vault, enable VM managed identity ("Key Vault Secrets User"), grant **Diana** (quarterly rotator)
  "Key Vault Secrets Officer", store `sql-password` + email creds. (SQL login expires every 3 months — B1.)

- **OQ-25 [user/IT] Recipient/config source = SharePoint `Config.xlsx`** (`…/Intacct SQL Feeds/Config.xlsx`).
  Notifier reads the recipient list from there at runtime (team self-serves). Same "build now, repoint later"
  approach: swappable `RecipientSource`, default = config list, switch to SharePoint read once **C6** access is
  granted. **Safeguards:** cache last-known-good list locally + a fallback admin address so a SharePoint-read
  failure never blocks a failure alert. **D1a RESOLVED 2026-07-02:** recipients in `Config.xlsx` → sheet
  **`Sheet1`**, table **`Table1`**; **one shared list** for both failure alerts and the daily report (read
  the Table1 rows via Graph workbook API). (Ties to OQ-12: `Config.xlsx`/`Env_data` is also the current env source.)

- **OQ-23 [ops] Log/audit retention = 1 week** (confirmed 2026-07-02). Implement logrotate (weekly) + prune
  `audit/*.json` older than 7 days.

- **OQ-24 [user] Email notifier** (confirmed 2026-07-02). Send **from** `timeclockplus@veritashcsolutions.com`;
  store mailbox credentials in Key Vault. Prefer Graph `sendMail` (app registration) over basic SMTP AUTH
  (M365 is retiring basic auth) — confirm which the mailbox supports (C5). Still need **D1** = recipient list
  for (a) failure alerts and (b) daily success report.
