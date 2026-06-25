# W3 + W5 Design — reconcile procs, config-driven adapters, full orchestration

Governing constraint: **nothing hardcoded.** Endpoints, table names, proc names, credentials, page
size, filters, log location, schedule, run strategy — all from config (`specs/06`) / secret store.
Open questions OQ-2/4/5 are exposed as **config knobs** (defaults = Make behavior), not baked in.

## W3 — reconcile + wipe in `sql_loader.py` (procs = black boxes; names from config)
- `wipe_window(start, end)`:
  - GL: `DELETE FROM {sql.gl_table} WHERE {sql.gl_delete_date_field} >= ? AND <= ?` (bound params).
  - AP: `DELETE FROM {sql.apbill_table}` if `sql.apbill_full_reload` else windowed on `{ap.date_field}`.
- `run_reconcile(start, end)`: `EXEC {sql.proc_sync_links} ?, ?` then `EXEC {sql.proc_update_vendor} ?, ?`
  (date params). Order fixed (specs/04 FR-LOAD-3).
- All identifiers come from `sql_cfg`; still `test_`-guarded.

## Config-driven adapters (lazy heavy imports so unit tests need no driver)
- `sage_transport.py`
  - `parse_login_response(payload) -> {"session_id"|"error"}` — PURE, testable with synthetic payloads.
  - `parse_query_response(payload, object_name) -> {"status_code","total","records","error"}` — PURE.
    Reads `_attributes.numremaining`/`totalcount`, JSON page records, Sage `errormessage`.
  - `make_http_transport(endpoint, *, timeout, gzip, session=None) -> transport(xml)` — thin `requests`
    wrapper (endpoint from `sage.url`); posts XML, hands body to the parsers. `requests` imported lazily.
- `db.py`
  - `build_connection_string(sql_cfg, secrets) -> str` — PURE; assembles from `sql.server`,
    `sql.database`, `sql.auth_mode` (`sql`|`windows`|`aad`), `sql.driver`, user/password (secret) or
    Trusted_Connection. Fully config-driven; testable without a driver.
  - `connect(sql_cfg, secrets) -> connection` — lazy `pyodbc.connect(build_connection_string(...))`.
- `logging_setup.py`
  - `build_logger(log_cfg, run_type) -> logger` writing to a **configurable location**
    (`log.dir`/`log.file_name_pattern`, sink `file`/`stdout`); run-log text mirrors Make messages
    (specs/07). No path hardcoded.

## Credentials (no hardcoding; secret store)
- `config.resolve_env_creds(cfg, env, secrets) -> creds dict` — maps an environment's
  `sage_creds_key` (e.g. `Prod_Sage_Evergreen`) to Sage creds pulled from the secret store by derived
  keys (e.g. `{key}_SENDER_ID`, `{key}_SENDER_PASSWORD`, ...). Names from config; values from env.

## W5 — `orchestrator.run(...)` (full, config-driven, injected factories)
Signature (factories/clock injected for testing):
`run(cfg, secrets, *, today, sage_client_factory, connect=db.connect, logger=None) -> RunResult`
1. **run_type + window**: `cfg.run.run_type_override` / `cfg.run.window_override` else derive from `today`
   (`dates`). `cfg.run.daily_lookback_months` honored.
2. **chunks**: `dates.month_chunks(start, end)`.
3. for each **enabled environment**:
   - creds = `resolve_env_creds`; client = `sage_client_factory(creds)`; conn = `connect(cfg.sql, secrets)`;
     loader = `SqlLoader(conn, cfg.sql)`.
   - **load strategy** (`sql.load_strategy`): `transaction` → do all work then `commit()`, `rollback()`
     on error (crash-safety, AC-9). `staging_swap` recognized (raises `NotImplementedError` until DBA
     provides staging — documented; default `transaction`).
   - `wipe_window(start, end)` (table names + gl delete date field + full-reload flag from config).
   - for each chunk: `fetch_gl`→`load_gl_records`; `fetch_ap`→`load_ap_records`; if
     `run.reconcile_granularity == "per_chunk"` → `run_reconcile(chunk)`.
   - if `reconcile_granularity == "per_window"` → `run_reconcile(start, end)` once after chunks.
   - capture per-env counts + errors; **isolation**: one env's failure rolls back/records, others continue.
4. return `RunResult` (per-env records/loaded/errors, run_type, window). Logged to the configured sink.

## CLI — `cli.py`
`main(argv)`: `--config PATH` (required), `--today YYYY-MM-DD` (optional override), `--run-type`,
`--start/--end` (backfill). Loads config, loads secrets from env/.env, runs orchestrator, exits non-zero
on any env error. No values hardcoded — all from config/flags.

## Tests (TDD, offline)
- W3: `wipe_window`/`run_reconcile` use configured names + bound params; full vs windowed AP delete.
- adapters: response parsers vs synthetic payloads; connection-string assembly per auth_mode; creds
  resolution from a fake secret map; logger writes to a tmp configurable location.
- W5: full run with FakeClient+FakeConn — counts, per-env isolation, transaction commit/rollback,
  reconcile granularity, and **config knobs flow through** (custom table/proc names, page size, filters).
