# 07 — Logging, Error Handling & Idempotency

## 7.1 Run log (replaces datastore `Log_text_SQLFeeds` 23497 + SharePoint upload 119)

Make accumulates a single text log across the run and uploads it to SharePoint at the end. The Python
rebuild produces an equivalent structured log. Log events (verbatim Make messages → rebuild events):

| Stage | Make module | Message (rebuild should preserve content) |
|---|---|---|
| Run start | 96 (mainflow) | `Start of process` |
| Window wipe | 99 (mainflow) | `{n} records deleted from GL table` / `{n} records deleted from APBill table` |
| Per-env start | 103 | `Instance{i}: Processing data for "{env}" environment` |
| GL loaded | 115 | `Instance{i}: {count} GL Records added to SQL for the period {start} to {end}` |
| GL error | 124 | `Instance{i}: Fetch GL Details error "{description2}"` |
| AP loaded | 116 | `Instance{i}: Fetched {count} APBill records for the period {start} to {end}` |
| AP error | 127 | `Instance{i}: Fetch APBill Details error "{description2}"` |
| Reconcile | 134 | `Instance{i}: Updated Vendor, Stampli links and PS links against the GL records in SQL` + `Processing for "{env}" complete` |
| Per-env done | 137 | `Instance{i} processing complete` |
| Run end | 119 data | `End of process` |

- `instance` label only present for monthly (daily has none); rebuild: include when provided.
- Sink configurable (`log.sink`: file/db/stdout). SharePoint upload optional for parity (`log.sharepoint_upload`, OQ-6); filename pattern `{run_type}run_log_{MMDDYYYY}.txt`.
- Additionally emit **structured** records (per env/object: counts, durations, status) for NFR-8 observability — beyond Make's plain text.

## 7.2 Transient error handling (retries)

Make wraps each I/O module in a `builtin:Break` (retry **3×**, interval **15 min**, then auto-complete).
Modules with retry: all HTTP (login 12, GL probe 15/page 18, AP probe 27/page 31), both SQL bulk
queries (58/56), the `SyncAPLinksToGL` proc (70), Excel read (1), SharePoint upload (119), and the
initial SetVariables (2).

**Rebuild requirements (NFR-5):**
- Retry transient failures on every Sage HTTP call, SQL execution, and config/secret read.
- Defaults: `retry.count=3`. Interval configurable (`retry.interval_s`); 15 min is long — likely tune
  down with exponential backoff, but honor Sage rate-limit (`Retry-After`) when present.
- ⚠️ `UpdateVendorInfoInGL` (module 159) has **no** retry handler in Make. Decide whether to add one
  (recommended) — note as a deliberate improvement.
- "Auto-complete on exhaustion" (Make `retry:true`) means a failed module is **swallowed** so the run
  continues. The rebuild should instead **record the failure** (per-env/object) and continue other
  work, then surface a non-zero/degraded run status — do not silently succeed (NFR-8/NFR-9).

## 7.3 Sage application errors (in-flow, not retried)

Distinct from transport errors: a Sage response may carry an `errormessage` (e.g. invalid object,
bad session). Make branches (routers 122/125) and logs `description2`, skipping that object's load
while letting the other object/route proceed. Rebuild: same — capture the Sage error text, skip that
object, continue, mark run degraded.

## 7.4 Idempotency (NFR-6)

The pipeline is idempotent by construction: each run **deletes the refresh window then reloads it**
(GL windowed, APBill full). Re-running the same window yields the same end state. The rebuild must
preserve this (don't switch to blind append). Combined with FR-LOAD-5 transaction safety, a failed run
can simply be re-run.

## 7.5 Failure isolation (NFR-9)

- A failure in one environment must not corrupt another. Process environments independently; capture
  per-env status.
- With `load_strategy=transaction`/`staging_swap` (`04` FR-LOAD-5), a per-env/per-window failure rolls
  back only that unit; previously committed environments/windows remain valid.

## 7.6 Dead-letter / alerting

Make has `dlq:true` (failed executions queue for inspection). Rebuild equivalent: persist failed
windows/environments (with the input window + error) to a retry log/table, and alert on degraded runs
(email/monitoring — mechanism TBD, OQ-9). Replaces the silent-overage governance gap noted in
`../../Memory_Opti.txt`.
