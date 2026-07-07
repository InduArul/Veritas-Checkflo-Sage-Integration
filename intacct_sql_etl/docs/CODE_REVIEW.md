# Code Review — `intacct_sql_etl` (static analysis pass)

**Reviewer hat:** SonarQube / Sentry-style static review.
**Scope:** all 14 modules in `src/intacct_etl/`.
**Date:** 2026-06-25.
**Overall:** Solid architecture (pure functions, dependency injection, no secrets in code, bound
parameters). The findings below were **all fixed** on 2026-06-25 (tests-first), bringing the suite
from 114 → **140 tests, green**. See the *Resolution log* at the bottom.

> **STATUS: ✅ CLOSED — 0 open findings.** This is now a release gate: a wave cannot **Ship** while
> any code-review finding is open (see `WAVES.md` S-step / DoD and `CLAUDE.md` non-negotiable #8).

Severity: 🔴 Critical · 🟠 Major · 🟡 Minor · 🔵 Info/Convention

---

## A. Best-practice checklist (quick scorecard)

| # | Best practice | Status | Ref |
|---|---|---|---|
| 1 | Resources (DB conn/cursor) closed deterministically | 🔴 FAIL | CR-01, CR-02 |
| 2 | No swallowed exceptions | 🔴 FAIL | CR-03 |
| 3 | Standard logging framework (levels, timestamps) | 🟠 FAIL | CR-04 |
| 4 | Logging actually wired in the entrypoint | 🟠 FAIL | CR-05 |
| 5 | No hardcoded values that have a config knob | 🟠 FAIL | CR-06 |
| 6 | Output/markup encoding (XML escaping) | 🟠 FAIL | CR-07 |
| 7 | Safe XML parsing of external input | 🟡 PARTIAL | CR-08 |
| 8 | DRY — no duplicated logic/constants | 🟠 FAIL | CR-09 |
| 9 | No identifier/name shadowing | 🟡 FAIL | CR-10 |
| 10 | Single source for magic defaults | 🟡 FAIL | CR-11 |
| 11 | Function cognitive complexity within limits | 🟡 FAIL | CR-12 |
| 12 | Errors aggregated, not overwritten | 🟡 FAIL | CR-13 |
| 13 | No stringly-typed structures | 🔵 FAIL | CR-14 |
| 14 | Consistent, complete type hints | 🔵 PARTIAL | CR-15 |
| 15 | No dead / forward-declared code | 🟡 PARTIAL | CR-16 |
| 16 | Specific exception types caught | 🟡 PARTIAL | CR-17 |
| 17 | Retry/backoff present where configured | 🔵 KNOWN-GAP | CR-18 |
| 18 | SQL identifier injection guarded | 🟡 PARTIAL | CR-19 |
| 19 | Secrets never reachable by logs | 🔵 WATCH | CR-20 |
| 20 | Naming conventions (snake_case, descriptive) | 🟢 PASS (minor) | CR-21 |
| — | DI / testability / pure functions | 🟢 PASS | — |
| — | Secrets out of source & config | 🟢 PASS | — |
| — | Bound params for SQL *values* | 🟢 PASS | — |
| — | Docstrings + spec traceability | 🟢 PASS | — |

---

## B. Findings

### 🔴 Critical

**CR-01 — DB connection is never closed (resource leak).**
`orchestrator.run()` opens `conn = connect(cfg["sql"], secrets)` per environment (line 83), commits or
rolls back, but never calls `conn.close()`. Each environment / scheduled run leaks a pooled connection.
*Fix:* wrap per-env work in `try/finally: conn.close()`, or give `SqlLoader`/connection a context
manager (`with connect(...) as conn:`).

**CR-03 — Swallowed exception on rollback (Sentry anti-pattern).**
`orchestrator.run()` lines 116-117: `except Exception: pass`. A rollback failure (the *most* important
thing to know about — possible data corruption) vanishes silently. *Fix:* record it on the entry and
log it (`entry["rollback_error"] = str(e2)`); never `pass`.

### 🟠 Major

**CR-02 — Cursors never closed.** `SqlLoader._exec_proc_json`, `wipe_window`, `run_reconcile` create
cursors (`self.conn.cursor()`) that are never closed. Use `with self.conn.cursor() as cur:`.

**CR-04 — No standard logging framework.** The codebase uses `print()` (`cli.py` lines 64; `logging_setup`
stdout sink line 32) plus a custom in-memory `RunLog`. No log levels, no timestamps, no per-module logger,
no way to raise severity to a monitor. *Fix:* adopt stdlib `logging`; keep `RunLog` as a file handler/sink.

**CR-05 — Run logging not wired in the entrypoint.** `cli.main()` never constructs a `RunLog` and never
passes `run_log=` to `runner()`. So in a real run **no run-log file is produced** and the detailed count
lines are never recorded (this is the `◐ Partial` in the spec-vs-build matrix). *Fix:* build `RunLog` from
`cfg["log"]` in `cli.main`, pass it in, and have the orchestrator emit per-env/per-chunk count + error lines.

**CR-06 — Config values ignored = hidden hardcoding** (violates the project's #1 principle):
- `sage_query._control` hardcodes `<dtdversion>3.0</dtdversion>` (line 36) — `config sage.dtd_version` is never read.
- `sage_query.fmt_date` and `orchestrator._parse_mdy` hardcode `"%m/%d/%Y"` — `config run.date_format` is never read.
*Fix:* thread these config values through, or delete the dead config keys so the spec doesn't overstate configurability.

**CR-07 — XML built by string concatenation without escaping (injection / malformed request).**
In `sage_query`, `sender_id`, `user_id`, `company_id`, and `session_id` are interpolated raw into XML
(lines 33, 47-48, 84-85). A credential or company id containing `& < >` produces invalid XML or injects
markup. Passwords use `<![CDATA[…]]>` (safer) but still break on a literal `]]>`. *Fix:* escape with
`xml.sax.saxutils.escape`, or build with `ElementTree`.

**CR-09 — Duplicated test-target guard (DRY).** The same logic exists twice:
`config.py` (`_SQL_TARGET_KEYS`, `_bare_object_name`, `assert_test_targets`) and
`sql_loader.py` (`_TARGET_KEYS`, `_bare`, `_assert_test_targets`). Two copies of a *safety* check drift
apart over time. *Fix:* keep one (in `config`) and have `SqlLoader` call it.

### 🟡 Minor

**CR-08 — Unsafe XML parser for external input.** `sage_transport.xml_to_dict` uses
`xml.etree.ElementTree.fromstring` on the Sage HTTP response. For untrusted/MITM XML, prefer `defusedxml`
to harden against entity-expansion. (Low risk over TLS to Sage, but cheap to fix before go-live.)

**CR-10 — Name shadowing.** `pagination.offsets(page_count: int, …)` (line 36) — the parameter shadows the
module-level `page_count` function. Rename the param (e.g. `n_pages`).

**CR-11 — Magic default `2000` duplicated.** `pagination.DEFAULT_PAGE_SIZE = 2000` exists, but
`sage_client.fetch_gl/fetch_ap` and `sql_loader.load_gl_records/load_ap_records` re-hardcode `=2000` in
signatures. Reference the constant (or require it from config) so there's one source of truth.

**CR-12 — High cognitive complexity in `orchestrator.run`.** ~65 lines with a nested `try`, an env loop, a
chunk loop, and multiple branches. SonarQube would flag cognitive complexity. *Fix:* extract
`_process_environment(env, cfg, …) -> entry` — also makes per-env behaviour unit-testable in isolation.

**CR-13 — Error overwriting across chunks.** In the chunk loop, `entry["gl_error"] = gl_err` /
`entry["ap_error"] = ap_err` keep only the **last** chunk's error; an earlier chunk's failure is lost if a
later chunk succeeds-with-error. *Fix:* accumulate a list of `{chunk, error}`.

**CR-16 — Dead / forward-declared code.**
- `transform.sql_escape_quotes` — explicitly "reference-only", unused. Move to docs or delete.
- `sage_query._BLANK_CREDS` is *used* on lines 115/151 but *defined* at the bottom (line 172). Works at
  runtime but reads backwards — move to the top with the other constants.
- `orchestrator.run_gl_for_window` ("W1 helper … kept") — appears unused outside tests. Confirm it's still
  needed or remove.

**CR-17 — Over-broad top-level catch.** `orchestrator.run`'s `except Exception` is acceptable for per-env
isolation, but catch the *expected* set explicitly (`SageError`, `ConfigError`, `pyodbc.Error`) and
re-raise/flag truly unexpected ones differently, so programming bugs aren't silently turned into
"DEGRADED env".

**CR-19 — SQL identifier interpolation.** Table/proc/field names are f-string-interpolated into
`DELETE`/`EXEC` text (`sql_loader` lines 45, 77, 81, 85, 97-98). Values are bound (good), but identifiers
are not validatable by the driver. Risk is low (config-sourced, test_-guarded) — add a one-line allow-list
validator (`re.fullmatch(r"[\w\.\[\]]+", name)`) as defense-in-depth.

### 🔵 Info / Convention

**CR-14 — Stringly-typed result entry.** The per-env `entry` dict and its magic keys (`"gl_error"`,
`"ap_error"`, `"error"`) are repeated in `orchestrator`, `RunResult.ok`, and `cli`. Drift-prone. *Fix:*
make it a dataclass (e.g. `EnvResult`).

**CR-15 — Incomplete type hints.** `SageClient.transport`, `_fetch`'s `(records, error)` tuple return,
and `orchestrator.run(cfg, secrets, …)` are untyped. Add hints + run `mypy` in CI.

**CR-18 — Retry/backoff configured but not implemented.** `config retry.count/interval_s/reconcile_settle_s`
exist; no code reads them (known W8 item). Flag clearly so it isn't mistaken for implemented.

**CR-20 — Secret-leak watch.** The full request XML contains passwords. Nothing logs it today, but add a
redaction helper now and an explicit rule: **never log raw request XML.** One careless debug line = leaked
credentials.

**CR-21 — Naming (minor).** Several cryptic locals: `cm`/`cu`/`m`/`u`/`h`/`k` (`reconcile.py`),
`cs`/`ce`/`wo`/`rt_override` (`orchestrator.py`), `pw` (`db.py`). Acceptable but `cm → checksum_make`
etc. improves readability.

---

## C. Suggested fix order (TDD — test first per CLAUDE.md)

1. **CR-01 + CR-02 + CR-03** (resource leaks + swallowed rollback) — reliability, smallest blast radius.
2. **CR-05 + CR-04** (wire RunLog in CLI; move to `logging`) — needed for the live parity run to be auditable.
3. **CR-06 + CR-07** (config-not-wired + XML escaping) — correctness before hitting live Sage.
4. **CR-09 + CR-12 + CR-13** (DRY guard, extract `_process_environment`, error aggregation) — maintainability.
5. **CR-08, CR-10, CR-11, CR-14..CR-21** — polish / convention sweep.

> Each fix: write the failing test first (e.g. `test_orchestrator_closes_connection`,
> `test_xml_escapes_special_chars`, `test_cli_writes_run_log`), see red, then implement to green.

---

## D. Resolution log (2026-06-25 — all findings closed, tests-first)

| ID | Fix (module) | Gating test | Status |
|---|---|---|---|
| CR-01 | `orchestrator._process_environment` closes conn in `finally` | `test_run_closes_connection_on_success/_on_failure` | ✅ |
| CR-02 | `SqlLoader` uses `contextlib.closing(cursor)` | `test_load_closes_cursor` | ✅ |
| CR-03 | rollback failure recorded as `entry["rollback_error"]` + logged | `test_rollback_failure_is_recorded_not_swallowed` | ✅ |
| CR-04 | stdlib `logging` in cli/orchestrator/retry; `basicConfig` from `log.level` | `test_main_*` | ✅ |
| CR-05 | `cli.main` builds `RunLog`, passes to runner, flushes in `finally` | `test_main_builds_and_flushes_run_log` | ✅ |
| CR-06 | `dtd_version`/`date_format`/gl+ap query opts wired from config | `test_dtd_version_is_configurable`, `test_date_format_is_configurable`, `test_query_options_flow_from_config` | ✅ |
| CR-07 | `sage_query._x` (escape) + `_cdata` (neutralize `]]>`) on all interpolated XML | `test_login_xml_escapes_special_chars_in_identifiers`, `test_password_in_cdata_is_neutralized` | ✅ |
| CR-08 | `xml_to_dict` rejects `<!DOCTYPE>/<!ENTITY>` | `test_xml_to_dict_rejects_doctype` | ✅ |
| CR-09 | single shared `config.assert_test_sql_targets`; SqlLoader delegates | `test_assert_test_sql_targets_shared_guard_blocks_prod` | ✅ |
| CR-10 | `pagination.offsets(n_pages=…)` (no shadowing) | existing `test_offsets` | ✅ |
| CR-11 | `DEFAULT_PAGE_SIZE` referenced in client + loader (no duplicate `2000`) | existing pagination/loader tests | ✅ |
| CR-12 | extracted `orchestrator._process_environment` (lower complexity) | full orchestrator suite | ✅ |
| CR-13 | per-chunk errors accumulate in `gl_errors`/`ap_errors` | `test_chunk_errors_are_aggregated` | ✅ |
| CR-14 | `_new_entry()` single result-schema factory | orchestrator suite | ✅ |
| CR-15 | type hints added (`SageClient`, `_fetch`, `run`, `retry`) | n/a (static) | ✅ |
| CR-16 | removed dead `sql_escape_quotes`; `_BLANK_CREDS` to top; `run_gl_for_window` kept (parity test) | parity dry-run test | ✅ |
| CR-17 | orchestrator logs unexpected exc w/ stack trace vs expected types | `test_env_failure_isolated_and_rolled_back` | ✅ |
| CR-18 | `retry.py` wired into `SageClient`; settle-sleep before reconcile | `test_retry.py` (4), `test_settle_sleep_before_reconcile` | ✅ |
| CR-19 | `config.safe_identifier` validates every interpolated identifier | `test_safe_identifier_*`, `test_wipe_window_rejects_unsafe_identifier` | ✅ |
| CR-20 | `sage_query.redact()` + "never log raw XML" rule | `test_redact_masks_passwords` | ✅ |
| CR-21 | cryptic locals renamed (`reconcile.py`) | existing reconcile tests | ✅ |

**Verification:** `PYTHONPATH=src python -m pytest -q` → **140 passed**.

---

## E. Audit-trail layer (2026-06-25 — added, self-reviewed clean)

New observability layer per `specs/12` (`audit.py`, `LoaderMetrics`, orchestrator/cli wiring).
Reviewed against the same checklist — **0 new findings**:
- **Logging:** uses stdlib `logging`; `write_audit` logs on failure.
- **Exception handling:** `write_audit` swallows at the I/O boundary **by design** (FR-AUD-7: audit must
  never fail the ETL) and logs the error — documented, not a silent swallow.
- **Resource mgmt:** files via `with`; no new connections/cursors.
- **No hardcoding:** sink/dir/format/patterns all config-driven (`audit.*`); clock/`now` injected.
- **Security:** counts/timings/identifiers only — no secrets/payloads (FR-AUD-8).
- **Naming/DRY/OOP:** dataclasses (`EnvAudit`/`RunAudit`/`LoaderMetrics`), `record_page` helper, no dupes.

**Verification:** `PYTHONPATH=src python -m pytest -q` → **152 passed** (140 + 12 audit tests).

---

## Review pass 2 — delta engine (specs/13) + secrets/notifier (specs/14)

**Date:** 2026-07-02. **Method:** workflow-backed review (4 finder angles + independent per-location
verifiers, high effort). **Scope:** this session's changes — `watermark.py`, `sql_loader.py`
(sweep), `sage_query.py` (delta/keys builders), `sage_client.py` (delta/keys fetch),
`orchestrator.py` (delta modes), `audit.py` (delete counts), `cli.py` (`--mode`, secrets/notify
wiring), `secret_provider.py`, `db.preflight`, `notify.py`.

**Outcome:** 10 distinct findings (9 confirmed, 2 plausible, 1 refuted). All **correctness/crash**
findings fixed tests-first (suite 220 → **229 green**); lower-severity items dispositioned below.

> **STATUS: ✅ CLOSED — 0 open correctness findings.**

Severity: 🔴 Critical · 🟠 Major · 🟡 Minor

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| CR-D1 | 🔴 | `sweep_ap` compared the FULL AP table to a window-scoped key set → deleted all bills outside the window | **FIXED (revised — user-caught follow-up 2026-07-02)** — the first fix (full-history compare) still broke *accumulate* (in 2027 it would delete 2024 rows Intacct no longer returns). Final: AP sweep is **windowed on `apbill_sweep_date_field`** like GL, or **SKIPPED** if no date column (default); plus a **mass-delete guard** (`sweep_max_delete_fraction`, aborts+rolls back). Tests `test_ap_sweep_skipped_without_date_field`, `test_ap_sweep_windowed_when_date_field_configured`, `test_sweep_gl_aborts_on_mass_delete`. Windowed AP sweep needs a DBA date column → OQ-26. |
| CR-D2 | 🔴 | Empty (error-free) key pull → every in-scope row judged missing → mass delete | **FIXED** — skip sweep when key set empty (mass-delete guard); test `test_sweep_skipped_when_key_pull_is_empty` |
| CR-D3 | 🔴 | Notifier `transport: graph` (documented default) raised at construction → aborted the whole run | **FIXED** — `GraphTransport` never raises at build (send is best-effort); CLI wraps notifier setup; tests `test_from_config_graph_transport_does_not_raise_at_build`, `test_graph_transport_send_is_best_effort_not_a_crash` |
| CR-D4 | 🟠 | Delta path never ran `run_reconcile` → GL link/vendor diverge from full refresh (parity break) | **FIXED** — reconcile runs in the upsert branch; test `test_upsert_runs_reconcile` |
| CR-D5 | 🟠 | Missing `watermark_table` read before try → whole-run crash instead of per-env isolation | **FIXED** — config read moved inside try; test `test_delta_missing_watermark_table_is_isolated_not_a_crash` |
| CR-D6 | 🟠 | Cutoff used server-local tz but WHENMODIFIED filter is company-tz → skipped records past grace | **FIXED** — `_effective_now` computes cutoff in `run.timezone` (company tz); tests `test_effective_now_*`. Live confirmation of Sage's WHENMODIFIED tz remains OQ-16b. |
| CR-D7 | 🟠 | Delta ignored the `load_strategy` guard the full path enforces | **FIXED** — delta raises `NotImplementedError` on non-`transaction` strategy; test `test_delta_unsupported_load_strategy_marks_error` |
| CR-D8 | 🟡 | `sweep_gl` SQL SELECT has no BOOKID filter while GL key pull is ACCRUAL-only | **ACCEPTED** — invariant: the GL table only ever holds ACCRUAL rows (every load path filters BOOKID=ACCRUAL), so no non-ACCRUAL row exists to mis-delete. Revisit if a non-ACCRUAL book is ever loaded. |
| CR-D9 | 🟡 | `Entry_Date <= end` binds a midnight `date`; DATETIME rows late on `end` could be missed | **ACCEPTED (matches existing)** — identical to the full-refresh `wipe_window` boundary; `Entry_Date` treated as a date. Revisit if it becomes a time-bearing DATETIME (add +1 day exclusive bound). |
| CR-D10 | 🟡 | Sweeps issue one DELETE per missing key (N round-trips) | **DEFERRED (perf)** — with CR-D1/D2 fixed, deletions are rare/small. Batch via temp-table anti-join if a large sweep ever shows up. |
| — | — | (refuted) CachingRecipientSource "dead" on config path | **NO CHANGE** — cache+fallback is intentional defense; not a defect. |

**Verification:** `python -m pytest -q` → **229 passed**.

---

## Review pass 3 — bounded-memory chunked delta (specs/13 §13.16)

**Date:** 2026-07-05. **Method:** manual adversarial review (logging / exception handling / resource
mgmt / dead code / naming-DRY / injection) + full suite. **Trigger:** 2026-07-02 VM overload —
`sage_client._fetch()` accumulates the whole window into one list; a wide delta `sweep-upsert`
(~600k GL keys) exhausted a swapless shared VM. **Scope:** `dates.month_chunks_dt` +
`dates.group_chunks` (new), `orchestrator._process_environment_delta` (upsert + sweep now loop
calendar-month chunks, extract→apply→`del`→next), `run.chunk_months` config, `deploy/run-etl.sh`
(operational cgroup/flock guard).

**Outcome:** 5 findings — **0 open correctness**; all accepted/no-change with rationale + gating tests.

> **STATUS: ✅ CLOSED — 0 open correctness findings.** Suite **249 green**.

Severity: 🔴 Critical · 🟠 Major · 🟡 Minor

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| CR-M1 | 🟡 | Empty-key-pull skip is now **per chunk**: a month whose Intacct key pull is empty is skipped, so a *fully-emptied* month's stale SQL rows would not be swept (differs from whole-window sweep, which would delete them as "missing"). | **ACCEPTED** — consistent with the existing mass-delete-guard philosophy (empty pull = suspicious, don't delete). Per OQ-20 hard-deletes occur only in **open** periods (which always have activity), so a fully-emptied GL month is not realistic. Specified in §13.16. Covered by `test_sweep_skipped_when_key_pull_is_empty`. |
| CR-M2 | 🟡 | `sweep_max_delete_fraction` guard now evaluated **per chunk** (smaller row base) → tighter; a small month with a high *genuine* deletion fraction could trip `SweepGuardError` and roll the run back. | **ACCEPTED** — tighter guard is safer-by-default; the fraction is configurable, and a re-run after raising it recovers (idempotent). Documented in §13.16. |
| CR-M3 | 🟡 | `month_chunks_dt(since, cutoff)` returns `[]` when `since >= cutoff` (watermark ≥1h ahead of cutoff, e.g. clock skew) → no fetch, but the completeness gate still advances the watermark to cutoff. | **NO CHANGE** — identical to the pre-chunk behaviour (it advanced unconditionally on a clean fetch); moving the watermark to cutoff only causes an idempotent re-pull next run. Not a regression. |
| CR-M4 | 🟠 | Chunk-boundary partition correctness: could a record be dropped or double-counted at a month boundary? | **VERIFIED CORRECT** — `month_chunks_dt` yields shared boundaries `(since,b1](b1,b2]…(bn,cutoff]`; the builder filters strict `> lower` / `<= upper`, so a record at exactly `b1` is in chunk 1 only. Proven by `test_month_chunks_dt_*` + `test_chunked_sweep_parity_deletes_exactly_the_missing_union` + `test_upsert_streams_fetch_then_load_per_chunk_bounded_memory`. |
| CR-M5 | 🟡 | Chunking multiplies Sage round-trips (one probe+paging per month) — a 30-month seed does 30 probes vs 1. | **ACCEPTED (perf)** — bounded memory is the priority; `chunk_months` raises the chunk size to trade RAM for fewer round-trips. Same disposition family as CR-D10. |

**Injection/resource:** no new SQL string-building — sweep/upsert still bind dates + JSON as params,
identifiers `safe_identifier`-validated; per-chunk `del` makes the release explicit; conn/loader still
closed in `finally`. **Logging:** per-chunk warnings carry the chunk bounds. **DRY:** reuses the same
`dates.month_chunks` tiling the full-refresh path already uses.

**Verification:** `PYTHONPATH=src python -m pytest -q` → **249 passed**.

## Review pass 4 — W8 observability: delete-key audit + honest rows_affected

**Date:** 2026-07-07. **Method:** manual adversarial review (logging / exception handling / resource
mgmt / dead code / naming-DRY / injection) + full suite. **Trigger:** #12 soak review found two audit
gaps — the delete-sweep records only a *count* (not which keys), and `rows_affected` is `0` in every
run because `_exec_proc_json` clamped the proc's `-1` ("no rowcount") to `0`. **Scope:**
`sql_loader.LoaderMetrics` (+`*_deleted_keys`, `*_rows_affected_known`), `record_page` (flag unknown
instead of faking 0), `_exec_proc_json` (return raw rowcount), `sweep_gl`/`sweep_ap` (capture keys),
`audit.EnvAudit` (+fields) + `audit.write_deletes_log` (new), `orchestrator._env_audit` (surface),
`cli` (emit deletes log). Deferred (flagged): A3 archive tables (DBA), insert/update split (OQ-15).

**Outcome:** 4 findings — **0 open correctness**; all accepted/verified with rationale + gating tests.

> **STATUS: ✅ CLOSED — 0 open correctness findings.** Suite **270 green** (9 new W8 tests).

Severity: 🔴 Critical · 🟠 Major · 🟡 Minor

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| CR-O1 | 🟠 | Partial-page metric: a batch with some pages returning a real count and some returning `-1` — is the run mislabelled? | **VERIFIED CORRECT** — `record_page` only ever SETs `*_known=False`, never back to True, so any single uncaptured page makes the whole object `known=False` (conservative). Numeric stays an int (unknown pages add 0). Proven by `test_rowcount_unknown_not_clamped_to_zero` + `test_rowcount_real_change_counted_and_known`. |
| CR-O2 | 🟡 | Deleted keys held in memory **and** written into the audit JSON — a very large sweep could bloat both. | **ACCEPTED** — delete-detection removes only rows gone from Intacct in **open** periods (inherently small; last soak sweep = 8), and `_guard_mass_delete` (`max_delete_fraction`) aborts an anomalous mass-delete **before** keys are captured. Same bounded-cost family as CR-M5. |
| CR-O3 | 🟡 | `write_deletes_log` opens in append mode → a re-run could duplicate lines. | **ACCEPTED** — `run_id` is a per-invocation timestamp; an idempotent re-run gets a new `run_id`/file. File name is `deletes_{run_id}.jsonl`. |
| CR-O4 | 🟡 | Deletes log defaults to the audit `dir` (`./audit`), not `logs/` as the design note first sketched. | **NO CHANGE** — co-located with the audit JSON it complements; overridable via `audit.deletes_dir`. Design note updated to match. |

**Injection/resource:** no new SQL string-building — deleted keys come from already-bound DB reads and
are written as JSON (no interpolation); `write_deletes_log` uses a `with` context and **never raises**
(FR-AUD-7, guarded — regression-tested by `test_main_persists_audit_trail`). **Logging:** cli logs the
deletes-log path. **DRY:** `write_deletes_log` mirrors `write_audit`; `record_page` unchanged for the
happy path. **Data-neutral:** load/sweep SQL + committed rows are byte-identical → parity unaffected
(43 reconciliation/integration tests green).

**Verification:** `PYTHONPATH=src python -m pytest -q` → **270 passed**.
