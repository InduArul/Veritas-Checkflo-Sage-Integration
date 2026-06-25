# 01 — Functional & Non-Functional Requirements

Consolidated, numbered requirements. Detail/derivation in the referenced specs. Every requirement is
traceable to a Make module (see `08_make_reference_map.md`).

## Functional Requirements

### Orchestration (see `05`)
- **FR-1** Derive `run_type` from the run date: `monthly` if day-of-month == 1, else `daily`. Overridable via config/CLI.
- **FR-2** Derive the refresh window from the run date:
  - daily → `[1st of (3 months ago), end of current month]`
  - monthly → `[Jan 1 of last year, end of current month]`
  - Overridable for manual backfills (explicit start/end).
- **FR-3** Slice the window into **calendar-month chunks**; process each chunk through the worker. (In Python this is an in-process loop, not HTTP fan-out; the 30s inter-chunk sleep is dropped.)
- **FR-4** Run twice daily on a schedule (~08:00 and ~17:30, configurable) and on demand.
- **FR-5** Process **every environment** listed in config (today: `Config.xlsx`/`Env_data`), each with its own Sage credentials (`Prod_Sage_<env>`).

### Extraction (see `02`)
- **FR-6** Authenticate to Sage Intacct once per environment (getAPISession); reuse the session id for all calls.
- **FR-7** Extract `GLDETAIL` for the window: exact 17-field SELECT, filter `BATCH_DATE` in window **AND** `BOOKID='ACCRUAL'`, order by `AUWHENCREATED, RECORDNO, LINE_NO`.
- **FR-8** Extract `APBILL` for the window: 7-field SELECT, filter `WHENPOSTED` in window, `showprivate=true`, order by `RECORDNO`.
- **FR-9** Paginate via offset/pagesize (size 2000): probe (pagesize 1) for count, then loop offsets. GL count = `numremaining+1`; AP count = `totalcount`.
- **FR-10** Skip empty/failed pages (data length 0 or statusCode ≠ 200). On a Sage `errormessage` for an object, skip that object's load and log it — do not abort the run.

### Transform (see `03`)
- **FR-11** Serialize each page to compact JSON and load it as-is (no client-side field mapping/renaming). Use bound parameters (preferred) instead of quote-escaped string interpolation.
- **FR-12** Preserve the business filters as configurable values: `BOOKID` (default `ACCRUAL`), GL date field (`BATCH_DATE`), AP date field (`WHENPOSTED`), `showprivate`.

### Load (see `04`)
- **FR-13** Before loading, wipe the destination window: `GLTable` where `Entry_Date` in window; `APBillTable` fully.
- **FR-14** Load GL pages via `AddGLDetailBulk(@json)`; AP pages via `AddAPBillBulk(@json)` (reuse existing procs by default).
- **FR-15** After both loads complete for the window, run `SyncAPLinksToGL(start,end)` then `UpdateVendorInfoInGL(start,end)`.
- **FR-16** The four stored procedures (`AddGLDetailBulk`, `AddAPBillBulk`, `SyncAPLinksToGL`, `UpdateVendorInfoInGL`) are part of the system; their definitions must be captured under `reference/sql/`.

### Logging (see `07`)
- **FR-17** Maintain a per-run log mirroring today's content (start, per-env processing, GL/AP counts + period, errors, reconcile, completion). Persist it (file/table; SharePoint upload optional — see OQ-6).

## Non-Functional Requirements

- **NFR-1 (Parity)** Output must match the Make pipeline for the same window: same rows + values in `GLTable`/`APBillTable` (row-count + checksum parity — `09`).
- **NFR-2 (Crash-safety)** A failure mid-load must leave the live tables in their pre-run state (transaction or staging+swap) — improvement over Make (`04` FR-LOAD-5).
- **NFR-3 (Configurable)** No hardcoded connections, credentials, environments, filters, sizes, or schedules — all via config (`06`).
- **NFR-4 (Secrets)** Credentials never in source or logs; sourced from a secret store / config excluded from VCS (`06`, `07`).
- **NFR-5 (Resilience)** Retry transient HTTP/SQL/IO failures (default 3×; interval configurable — Make uses 15-min). Honor Sage rate limits.
- **NFR-6 (Idempotency)** Re-running a window reproduces the same end state (windowed delete + reload makes this naturally idempotent).
- **NFR-7 (Cost)** Runs on existing SQL Server + Sage API entitlement; $0 new recurring cost (project rationale).
- **NFR-8 (Observability)** Clear run status, per-environment/per-object record counts, and error surfacing for unattended scheduled runs.
- **NFR-9 (Multi-environment isolation)** A failure in one environment must not silently corrupt another; per-env error capture.
