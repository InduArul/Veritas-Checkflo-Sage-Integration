# W0 Design — Foundations (component contracts = ETL spec for derivation)

Traces to: specs/05 (§5.2/5.5), specs/02 (§FR-EXT-4), specs/06. These contracts are the basis for the
W0 unit tests (TDD). Pure functions where possible (no I/O) so they are deterministic + offline-testable.

## Package layout (`src/intacct_etl/`)
`dates.py`, `pagination.py`, `config.py` (W0) · later: `sage_query.py`, `sage_client.py`,
`transform.py`, `sql_loader.py`, `reconcile.py`, `orchestrator.py`, `cli.py`, `logging_setup.py`.

## `dates.py` — run-type, window, month-slicing (spec 05 §5.2/5.5)
- `derive_run_type(today: date) -> "daily"|"monthly"` — `monthly` iff `today.day == 1`, else `daily`.
  (Make: run_type 2 vs 1.)
- `last_day_of_month(d: date) -> date`.
- `subtract_months(d: date, n: int) -> date` — month arithmetic, day clamped.
- `derive_window(today, daily_lookback_months=3) -> (start: date, end: date)`:
  - `end = last_day_of_month(today)` (Make `Today_date`).
  - `start =` if `today.day != 1`: first day of month `today - daily_lookback_months` (Make `MM/01` of addMonths(now,-3));
    else `date(today.year-1, 1, 1)` (Make Jan-1-last-year).
- `month_chunks(start: date, end: date) -> list[(date, date)]` — contiguous **calendar-month** windows
  tiling `[start, end]`. First chunk starts at `start`, last ends at `end`.
  - **Equivalence note (parity-safe):** Make's loop produces contiguous windows whose *union* is exactly
    `[start, end]` (each next start = prev end + 1 day), but its `addMonths(prev_end,1)` drifts off
    month-end after February. Since the GL/AP Sage filters are inclusive range filters, the **union date
    range — hence the final loaded rows — is identical** whether we use Make's drift or clean calendar
    months. We use clean calendar months (simpler, obviously gap/overlap-free). Recorded vs specs/05.
- Override hooks (config, spec 06): `run.run_type_override`, `run.window_override` bypass derivation
  for backfills (handled in orchestrator; `dates.py` stays pure).

## `pagination.py` — page math (spec 02 §FR-EXT-4)
- `gl_total(numremaining: int) -> int` = `numremaining + 1` (GL probe basis).
- `ap_total(totalcount: int) -> int` = `totalcount` (AP probe basis).
- `page_count(total: int, page_size: int = 2000) -> int` = `1 if total <= page_size else ceil(total/page_size)` (min 1).
- `offsets(page_count: int, page_size: int = 2000) -> list[int]` = `[0, page_size, 2*page_size, ...]`.

## `config.py` — config + secrets (spec 06)
- `load_config(path) -> dict` — parse YAML, then `validate_config`.
- `validate_config(cfg) -> None` — raise `ConfigError` if a required section/key is missing or
  `environments` is empty. Required top-level: `run, environments, sage, gl, ap, sql, retry, log`.
- `resolve_secret(name, env=os.environ) -> str` — fetch a secret by key from env/`.env`; raise if absent.
  Secrets never read from the YAML itself (NFR-4).
- `is_test_mode(cfg) -> bool` and `assert_test_targets(cfg)` — guard: in dev/test, all SQL table/proc
  names MUST be `test_`-prefixed (defense-in-depth alongside the prod_write_guard hook). Raises if a
  non-`test_` target is configured while in test mode.

## Golden values for tests (from Make blueprint samples — authoritative)
- 8AM mainflow sample: `now=2026-04-01` → run_type `monthly`, window `(2025-01-01, 2026-04-30)`.
  (Sample showed run_type 2, Today_date 04/30/2026, Report_start_date 01/01/2025.)
- `now=2026-06-24` → run_type `daily`, window `(2026-03-01, 2026-06-30)`, 4 month-chunks.
- GL monthly probe sample `numremaining=580050` → total `580051`, `page_count=291` (size 2000).
- AP probe sample `totalcount=8321` → total `8321`, `page_count=5`, offsets `[0,2000,4000,6000,8000]`.
