# 10 — Open Questions (to confirm before/while implementing)

Resolve these before coding the affected area. Owner in brackets.

- **OQ-1 [DBA/user] Stored-procedure source.** We must capture the bodies of `AddGLDetailBulk`,
  `AddAPBillBulk`, `SyncAPLinksToGL`, `UpdateVendorInfoInGL` (`SELECT OBJECT_DEFINITION(OBJECT_ID('dbo.<proc>'))`)
  into `reference/sql/`. They define the real JSON→column mapping, types, and dedup/merge. **Decision:**
  reuse the procs as-is (default), or port to Python? Until captured, the GL/AP column mappings are
  proc-defined, not in this spec.

- **OQ-2 [DBA/user] GL delete vs extract date field.** Mainflow deletes `GLTable` on **`Entry_Date`**
  (module 135) but the Sage GL extract filters on **`BATCH_DATE`** (module 18). Are these the same basis?
  If not, the delete window and the reload window can diverge (orphan/duplicate rows). Confirm intended
  field for both.

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

- **OQ-12 [user] Env source of truth.** Today environments come from `Config.xlsx`/`Env_data`. Move to
  the YAML `environments[]` (default), or keep reading the Excel file? List the current env names (sample
  shows `Evergreen`; sample length = 2).

- **OQ-14 [Sage/dev] Live response wire shape.** The `sage_transport` parsers are written to the
  documented sample shapes (XML envelope; `_attributes.numremaining/totalcount`; records under the
  object element; `returnformat=json` payload tolerated as text/array). The exact live shape (XML vs
  JSON body, attribute key names, nesting) MUST be confirmed against a real Sage response and the
  parsers/`xml_to_dict` validated in an integration test before go-live.

- **OQ-15 [DBA] Insert vs update split for the audit trail.** The bulk procs
  (`AddGLDetailBulk`/`AddAPBillBulk`) merge via OPENJSON and return only a total `rowcount` (if any).
  The audit trail records `rows_affected` (total touched). A true inserted-vs-updated breakdown
  (`specs/12` FR-AUD-5) requires the proc to OUTPUT counts or return a result set. Confirm whether the
  procs can expose this; until then `rows_affected` is the monitored metric.

- **OQ-13 [DBA] GL count `numremaining+1`.** Confirm this correctly equals total rows in all cases (it
  assumes the pagesize-1 probe returns 1 row + N remaining). Edge case: zero-row windows → numremaining=0
  → total=1 but no data; the `length(data)>0` guard handles it, but verify the page loop count.
