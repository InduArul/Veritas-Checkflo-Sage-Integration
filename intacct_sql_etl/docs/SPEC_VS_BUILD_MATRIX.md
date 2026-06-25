# Spec ↔ Build Traceability Matrix

Step-by-step comparison of the **reverse-engineered Make behavior / spec** against **what is built**,
with the **test** that proves it and an honest **status**. Generated at end of the W0–W6 build
(114 tests green, offline). Use this as the punch-list before/at live cutover.

**Status legend**
- ✅ **Built + tested** offline (pure logic / injected fakes)
- 🔁 **Replaced by design** — Make's mechanism intentionally swapped for an equivalent (config / in-process). Behavior preserved.
- ◐ **Partial** — core built, a sub-behavior still missing
- ⏳ **Deferred** — needs live creds / DBA / W8 hardening
- ⬜ **Not built yet**

---

## A. Mainflow — scheduler / date-slicer / table-refresh (scenarios 1743529, 4066399)

| # | Make behavior (module) | Spec | Built (module.fn) | Test | Status |
|---|---|---|---|---|---|
| A1 | `run_type` = monthly on 1st else daily (mod 2) | 05 §5.2 | `dates.derive_run_type` | test_dates::test_derive_run_type | ✅ |
| A2 | Window: daily=3-mo-ago→month-end; monthly=Jan1-last-yr→month-end (mod 2) | 05 §5.2 | `dates.derive_window` | test_dates::test_window_* (golden from Make sample) | ✅ |
| A3 | Slice window into calendar-month chunks (mods 163/176 repeaters, 164/178 math) | 05 §5.5 | `dates.month_chunks` | test_dates::test_month_chunks_* (contiguity, count, clamp) | ✅ |
| A4 | Clamp last chunk end to `Today_date`; gate start≤today (171/169/179/180) | 05 §5.5 | `dates.month_chunks` (clamps to `end`) | test_dates::test_month_chunks_* | ✅ |
| A5 | 26 (monthly) / 5 (daily) repeat caps | 05 §5.5 | n/a — `month_chunks` emits exact months (no wasted no-ops) | test_month_chunks_monthly_window_count (=16) | 🔁 |
| A6 | DELETE GLTable WHERE Entry_Date in window (mod 135) | 04 FR-LOAD-1 | `sql_loader.wipe_window` (field+table from config) | test_sql_loader_w3::test_wipe_window_gl_windowed_ap_full | ✅ |
| A7 | DELETE APBillTable (all rows) (mod 136) | 04 FR-LOAD-1 | `sql_loader.wipe_window` (`apbill_full_reload`) | test_sql_loader_w3::test_wipe_window_* | ✅ |
| A8 | Per-chunk HTTP fan-out to worker (mods 173/161) | 05 §5.8 | in-process call (orchestrator loops chunks) | test_orchestrator_run::test_run_daily_window_and_counts | 🔁 (fan-out removed) |
| A9 | 30s `FunctionSleep` between chunks (mods 174/182) | 05 §5.8 | **dropped** (rate-limit plumbing only) | — | 🔁 |
| A10 | Run-log "Start of process" / deleted counts (mods 96/99) | 07 §7.1 | `logging_setup.RunLog` + orchestrator "Start of process" | test_logging::* | ◐ (start line yes; "N deleted" line not emitted) |
| A11 | Scheduled 08:00 + 17:30 (twice daily) | 05 §5.6 | external scheduler drives `cli` (cron/Task Scheduler) | — | ⏳ (OQ-8: tz/weekdays) |

---

## B. Worker — the ETL (scenarios 1744137 daily, 1744512 monthly; identical)

| # | Make behavior (module) | Spec | Built (module.fn) | Test | Status |
|---|---|---|---|---|---|
| B1 | Webhook input {start, end, Instance} (mod 158) | 02 FR-EXT | `orchestrator.run` args / `cli` flags | test_orchestrator_run::* | 🔁 (args, not webhook) |
| B2 | `Today_date`=end, start, instance_no (mod 2) | 03 FR-TR-1 | window passed per chunk; `instance_no`=log label | test_orchestrator_run | ◐ (instance label not replicated — logging only) |
| B3 | Environment list from Config.xlsx `Env_data` (mod 1) | 06 §6.1 | `config.environments[]` | test_config::* | 🔁 (OQ-12: YAML default) |
| B4 | Sage creds per env from datastore `Prod_Sage_<env>` (mod 3) | 06 §6.2 | `config.resolve_env_creds` (secret-store pattern) | test_db_and_creds::test_resolve_env_creds_* | ✅ |
| B5 | Login `getAPISession`; reuse sessionid (mod 12) | 02 FR-EXT-1 | `sage_query.build_login_xml` + `sage_client.login` + `sage_transport.parse_login_response` | test_sage_query, test_sage_client, test_sage_transport | ✅ |
| B6 | 3 routes GL ∥ AP ∥ reconcile (mod 13) | 02 FR-EXT-6 | `orchestrator.run` (GL→AP→reconcile per chunk) | test_orchestrator_run | ✅ |
| B7 | GL probe pagesize 1 → numremaining (mod 15) | 02 FR-EXT-2 | `build_gl_query_xml(page_size=1)` + `parse_query_response` | test_sage_query::test_gl_probe_*, test_sage_transport | ✅ |
| B8 | GL total = numremaining + 1 (mod 17) | 02 FR-EXT-2 | `pagination.gl_total` | test_pagination::test_gl_total | ✅ |
| B9 | GL query: 17 fields, BATCH_DATE window + BOOKID=ACCRUAL, orderby (mod 18) | 02 FR-EXT-2 | `build_gl_query_xml` | test_sage_query::test_gl_query_* | ✅ |
| B10 | Offset pagination, pagesize 2000 (mods 64/20/18) | 02 FR-EXT-4 | `pagination.page_count/offsets` + `sage_client.fetch_gl` | test_pagination, test_sage_client::test_fetch_gl_multi_page | ✅ |
| B11 | Empty/failed page guard (mod 140, statusCode==200) | 02 FR-EXT-4 | `sage_client._fetch` (status + non-empty) | test_sage_client::test_fetch_gl_zero_rows | ✅ |
| B12 | Page → JSON (mod 81) | 03 FR-TR-2 | `transform.encode_page` | test_transform::* | ✅ |
| B13 | SQL-escape quotes + offset+=2000 (mod 21) | 03 FR-TR-2 | **bound parameter** (no escape) + `pagination.offsets` | test_sql_loader::test_load_gl_uses_..bound_param | 🔁 (escape removed; safer) |
| B14 | EXEC AddGLDetailBulk @json (mod 58) | 04 FR-LOAD-2 | `sql_loader.load_gl_records` (proc from config) | test_sql_loader::*, test_sql_loader_w3 | ✅ |
| B15 | GL Sage-error → skip + log (mods 122/123/124) | 02 FR-EXT-5 | `sage_client` returns error; `orchestrator` records `gl_error`, continues | test_sage_client::test_fetch_gl_probe_error_skips_object | ◐ (skip ✅; detailed log line not emitted) |
| B16 | AP probe pagesize 1, 4 fields (mod 27) | 02 FR-EXT-3 | `build_ap_query_xml`, `AP_PROBE_FIELDS` | test_sage_query_ap::test_ap_page_has_seven_fields_probe_has_four | ✅ |
| B17 | AP total = totalcount (mod 28) | 02 FR-EXT-3 | `pagination.ap_total` | test_pagination::test_ap_total | ✅ |
| B18 | AP query: 7 fields, WHENPOSTED window, showprivate, orderby (mod 31) | 02 FR-EXT-3 | `build_ap_query_xml`, `AP_PAGE_FIELDS` | test_sage_query_ap::* | ✅ |
| B19 | AP offset pagination (mods 29/30/31) | 02 FR-EXT-4 | `sage_client.fetch_ap` | test_sage_client_ap::test_fetch_ap_uses_totalcount_basis | ✅ |
| B20 | AP page → JSON + EXEC AddAPBillBulk (mods 129/56) | 04 FR-LOAD-2 | `transform.encode_page` + `sql_loader.load_ap_records` | test_sql_loader_w3::test_load_ap_records_uses_configured_proc | ✅ |
| B21 | AP Sage-error → skip + log (mods 125/126/127) | 02 FR-EXT-5 | `orchestrator` records `ap_error`, continues | test_orchestrator (isolation) | ◐ (skip ✅; log line not emitted) |
| B22 | Settle sleep 3s before reconcile (mod 71) | 03 FR-TR-6 | config key `retry.reconcile_settle_s` exists | — | ⬜ (not wired; minor) |
| B23 | EXEC SyncAPLinksToGL @start,@end (mod 70) | 04 FR-LOAD-3 | `sql_loader.run_reconcile` (proc from config, bound dates) | test_sql_loader_w3::test_run_reconcile_* | ✅ |
| B24 | EXEC UpdateVendorInfoInGL @start,@end (mod 159) | 04 FR-LOAD-3 | `sql_loader.run_reconcile` | test_sql_loader_w3::* | ✅ |
| B25 | Reconcile per-chunk vs per-window | 04 / OQ-4 | `orchestrator` `reconcile_granularity` config | test_orchestrator_run::test_reconcile_granularity_* | ✅ |
| B26 | Datastore run-log lines: counts, period, errors (mods 102–137) | 07 §7.1 | `RunLog` supports; orchestrator emits start/complete only | test_logging | ◐ (detailed count/error lines not yet emitted) |
| B27 | SharePoint upload of run-log .txt (mod 119) | 07 §7.1 / OQ-6 | config flag `log.sharepoint_upload` only | — | ⏳ (uploader not built; optional) |
| B28 | onerror `Break` retry 3×/15min on all I/O (mods 143–155) | 07 §7.2 / NFR-5 | config `retry.*` keys present | — | ⏳ (retry wrapper = W8) |
| B29 | Per-env aggregation boundary (mod 110) | 05 | `orchestrator` per-env loop | test_orchestrator_run | 🔁 |
| B30 | Real HTTP/XML transport (parseResponse) | 02 | `sage_transport.make_http_transport` + `xml_to_dict` + parsers | test_sage_transport::* (parsers) | ◐ (built; live wire shape unverified — OQ-14) |

---

## C. Cross-cutting requirements

| # | Requirement | Spec | Built | Test | Status |
|---|---|---|---|---|---|
| C1 | **No hardcoding** — endpoints/tables/procs/creds/sizes/filters/log loc from config | NFR-3 | `config`, `sql_loader`, `sage_query`, `db`, `logging_setup` all parameterized | custom names asserted in test_sql_loader_w3, test_db_and_creds, test_orchestrator_run | ✅ |
| C2 | Secrets never in source/config; from secret store | NFR-4 | `config.resolve_secret/resolve_env_creds`; `.gitignore` | test_db_and_creds, test_config | ✅ |
| C3 | Idempotency (delete window + reload) | NFR-6 | `wipe_window` + reload | test_gl_parity_dryrun (stable); test_wipe_window | ✅ |
| C4 | Crash-safety: transaction, no half-empty tables | NFR-2 / AC-9 | `orchestrator` per-env commit/rollback | test_orchestrator_run::test_env_failure_isolated_and_rolled_back | ✅ (transaction); staging_swap ⏳ |
| C5 | Multi-environment isolation | NFR-9 | `orchestrator` per-env try/except | test_orchestrator_run::test_env_failure_isolated | ✅ |
| C6 | Parity = correctness (counts + checksum + sample) | 09 / 11 | `reconcile.reconcile/checksum` + dry-run gate | test_reconcile, test_gl_parity_dryrun | ✅ offline; **live ⏳** |
| C7 | Prod-table write protection | CLAUDE.md / 11 | `config.assert_test_targets` + `SqlLoader` guard + `prod_write_guard` hook | test_config, test_sql_loader::test_loader_rejects_prod_targets | ✅ |
| C8 | CLI entrypoint (scheduled + backfill) | 05 / 06 | `cli.main/parse_args` | test_cli::* | ✅ |
| C9 | `staging_swap` load strategy | 04 FR-LOAD-5 / OQ-5 | recognized; raises until provided | test_orchestrator_run::test_staging_swap_not_implemented | ⏳ |

---

## D. "No hardcoding" — Make-fixed value → config knob (confirmation of C1)

| Make-fixed value | Config key | Default |
|---|---|---|
| Sage endpoint URL | `sage.url` | api.intacct.com gateway |
| Page size 2000 | `sage.page_size` | 2000 |
| GL filter `BOOKID=ACCRUAL` | `gl.book_filter` | ACCRUAL |
| GL date field `BATCH_DATE` | `gl.date_field` | BATCH_DATE |
| AP date field `WHENPOSTED` | `ap.date_field` | WHENPOSTED |
| GL/AP select fields, orderby | `gl.*` / `ap.*` | per spec 02 |
| Table `Intacct.dbo.GLTable` / `APBillTable` | `sql.gl_table` / `sql.apbill_table` | (test_-prefixed in test_mode) |
| Procs Add*/Sync*/Update* | `sql.proc_*` | names |
| GL delete date field `Entry_Date` | `sql.gl_delete_date_field` | Entry_Date (OQ-2) |
| APBill full wipe | `sql.apbill_full_reload` | true |
| SQL server/db/auth/creds | `sql.server/database/auth_mode/...` + secret store | — |
| Daily lookback 3 months | `run.daily_lookback_months` | 3 |
| Reconcile per-chunk | `run.reconcile_granularity` | per_chunk (OQ-4) |
| Retry 3×/15min | `retry.count` / `retry.interval_s` | 3 / (wrapper W8) |
| Log location / SharePoint folder | `log.dir` / `log.file_name_pattern` / `log.sharepoint_*` | ./logs |
| Schedule 08:00/17:30 | `run.schedule_times` (external scheduler) | (OQ-8) |

---

## E. Punch-list for tomorrow (live creds) and W8

| Item | From | Needs |
|---|---|---|
| Live Sage wire-shape validation of parsers | B30 / OQ-14 | one real Sage response → confirm `xml_to_dict`/parser paths |
| Real SQL load via `test_` tables + `test_` procs | B14/B20/B23/B24 | OQ-1 (test_ procs), OQ-11 (SQL creds) |
| **Live parity vs Make** into `test_` tables | C6 / 09 §9.3 | creds + a fixture window |
| Retry/backoff wrapper on all I/O | B28 | W8 |
| Detailed run-log lines (counts/period/errors) + optional SharePoint upload | A10/B26/B27 | W8 (+OQ-6) |
| Settle sleep before reconcile (config-wired) | B22 | W8 (minor) |
| `staging_swap` strategy | C9 / OQ-5 | DBA decision |
| Scheduling (cron/Task Scheduler) twice daily | A11 / OQ-8 | tz/weekdays |
| GL delete vs extract date-field reconciliation | A6 / OQ-2 | DBA confirm |

---

## Summary

| Status | Count (of ~50 mapped behaviors) | Meaning |
|---|---|---|
| ✅ Built + tested (offline) | ~33 | core extract/transform/load/orchestration/parity/config |
| 🔁 Replaced by design | ~7 | fan-out, sleeps, escape, Excel→config, webhook→args (behavior-equivalent) |
| ◐ Partial | ~5 | logging detail lines, instance label, live wire shape |
| ⏳ / ⬜ Deferred | ~6 | retry, SharePoint upload, staging_swap, scheduling, settle-sleep, **live parity** |

**Bottom line:** every requirement in the reverse-engineered spec is **accounted for** — built+tested,
deliberately replaced, or explicitly deferred with a reason. No silent gaps. The deferred items are
exactly the ones that require the creds/DBA inputs arriving tomorrow, plus W8 hardening.
