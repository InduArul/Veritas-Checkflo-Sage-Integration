# 12 — Audit Trail (run/step observability)

Purpose: emit a **structured, analyzable record** of every ETL run so we can monitor health and
performance — what ran, when, how much data moved through each step, what failed, and how long each
of the three ETL steps took. This complements the human-readable run log (`specs/07`); the audit
trail is machine-readable (JSON/JSONL) and meant for analysis/trending.

## Requirements

- **FR-AUD-1 — Per-run record.** Each run produces one audit record containing: `run_id`,
  `run_type` (daily/monthly), refresh window (`start`/`end`), `started_at`/`finished_at`
  (timestamps), total `duration_s`, overall `ok`, and a list of per-environment records.

- **FR-AUD-2 — Per-environment record.** For each environment: `name`, `status`
  (`ok`/`degraded`/`failed`), `started_at`/`finished_at`, `duration_s`, counts, per-step durations
  (below), `error`/`rollback_error` (if any), and the per-chunk error lists (`gl_errors`/`ap_errors`).

- **FR-AUD-3 — Counts.** Capture, per environment and object (GL, AP):
  - `*_read` — rows extracted from Sage ("how many read", + when via `started_at`).
  - `*_loaded` — rows sent to the load proc **and committed** (0 if the env rolled back).
  - `*_rows_affected` — rows the proc reported touched (`cursor.rowcount`); proxy for inserted+updated.
  - `*_pages` — number of load batches.
  - `failed` — rows that did not land (all rows of an environment that errored and rolled back).
  - Run-level `totals`: environments, read, loaded, rows_affected, failed (summed).

- **FR-AUD-4 — Per-step timing (the 3 ETL steps).** Per environment, separate durations for:
  - **Extract** (`extract_s`) — Sage fetch (probe + pages) wall time.
  - **Transform** (`transform_s`) — page JSON encoding time.
  - **Load** (`load_s`) — DB proc-execution time; plus `wipe_s` (pre-load delete) and
    `reconcile_s` (post-load link/vendor procs) tracked separately.

- **FR-AUD-5 — Insert/update split (OQ).** The bulk procs are black boxes and do not return an
  insert-vs-update breakdown. We record `rows_affected` (total touched). A true split requires proc
  support — tracked as **OQ-15**; until then `rows_affected` is the monitored figure.

- **FR-AUD-6 — Configurable sink (no hardcoding).** Audit output is fully config-driven
  (`audit` section, `specs/06`): `enabled`, `sink` (file/stdout), `dir`, `format` (`json` per-run
  file | `jsonl` appended log), and file-name pattern. Default: enabled, one JSON file per run under
  a configurable `dir`, plus optional JSONL append for trending. No path hardcoded.

- **FR-AUD-7 — Never block the run.** Audit capture/write must never fail the ETL; a write error is
  logged (stdlib logging) and swallowed at the boundary, not propagated.

- **FR-AUD-8 — No secrets.** The audit record contains counts/timings/identifiers only — never
  credentials, connection strings, or raw payloads (`specs/06` secret rules).

## Notes
- Timestamps and the monotonic clock are injected (deterministic tests); production uses wall clock
  + `time.monotonic` for durations.
- The audit trail is emitted in addition to the run log and stdlib logging; all three are
  consistent for a given run.
