# 09 — Acceptance Criteria & Parity Validation

The rebuild is accepted when it produces the **same data** as the Make pipeline for the same window,
plus the agreed reliability improvements. Mirrors the parallel-run + checksum approach in
`../../Memory_Opti.txt` §6–7.

## 9.1 Functional parity (must pass)

- **AC-1 Row-count parity.** For a fixed window/environment, `COUNT(*)` of `GLTable` and `APBillTable`
  after the Python run == after the Make run (same delete+reload semantics).
- **AC-2 Value/checksum parity.** A column-wise checksum (e.g. `HASHBYTES` over ordered key columns, or
  `CHECKSUM_AGG`) over the loaded rows matches between Python and Make for the same window. Covers
  GLTable post-`AddGLDetailBulk` and APBillTable post-`AddAPBillBulk`.
- **AC-3 Reconcile parity.** After `SyncAPLinksToGL` + `UpdateVendorInfoInGL`, the GL rows' `Stampli_Link`,
  `Platform_Services_Link`, vendor id/name match Make's result for the window.
- **AC-4 Filter fidelity.** Only `BOOKID='ACCRUAL'` GL rows in the GL date window, and AP rows in the AP
  date window with `showprivate` semantics, are present — verified by spot queries.
- **AC-5 Pagination completeness.** Total loaded rows == Sage `totalcount` for the window (no page lost
  at offset boundaries; verify a window with >2000 rows so multiple pages exercise).
- **AC-6 Multi-environment.** Each configured environment is loaded with its own credentials; counts per
  env match Make.
- **AC-7 run_type/window derivation.** Given a run date, the derived run_type + window match `05 §5.2/5.5`
  (unit-tested across edge cases: 1st of month, mid-month, year boundary, month with 28/30/31 days).
- **AC-8 Idempotency.** Running the same window twice yields identical table state (AC-1/AC-2 stable).

## 9.2 Reliability / non-functional (must pass)

- **AC-9 Crash-safety.** Injecting a failure mid-load leaves the live tables in their pre-run state
  (transaction rollback / staging not swapped) — the headline improvement over Make.
- **AC-10 Retry.** Transient HTTP/SQL failures are retried per config; a Sage `errormessage` skips that
  object and is logged without aborting the run.
- **AC-11 No secrets in repo/logs.** Static scan: no credentials in source, config (non-secret), or
  emitted logs.
- **AC-12 Config-only changes.** Switching environment, page size, `BOOKID`, date fields, schedule, and
  SQL target requires **no code change** (config only).
- **AC-13 Observability.** Run emits per-env/per-object counts + status; degraded runs are distinguishable
  from clean runs (non-zero/flagged status), unlike Make's silent auto-complete.

## 9.3 Validation procedure (parallel run)

1. Pick a **representative window** with multi-page GL (>2000 rows) and ≥2 environments.
2. Snapshot Make's result (it currently populates the tables) — capture row counts + checksums per table
   (and a small sample of full rows) **before** switching off Make.
3. Run the Python pipeline against a **staging copy** of `Intacct` (or a clearly separated schema) for
   the same window; compute the same metrics.
4. Diff: AC-1..AC-6 must match exactly. Investigate any delta (likely culprits: date field mismatch
   OQ-2, AP probe-vs-page field set, proc reuse, JSON key casing).
5. Repeat for: a daily window, a monthly (1st-of-month) full reload, a window with a Sage error, and a
   zero-row window.
6. Run **several cycles in parallel** with Make (Python writing to staging) before cutover; then cut over
   and keep Make scenarios inactive for rollback (per Memory_Opti next-steps).

## 9.4 Out of scope for v1 (explicit)
- Re-implementing the four stored procedures in Python (default = reuse them; OQ-1).
- SharePoint log upload unless `log.sharepoint_upload` is enabled (OQ-6).
- Bank Feeds Generation/Reconciliation scenarios (Phase 2 — `../../Memory_Opti.txt` §5).
