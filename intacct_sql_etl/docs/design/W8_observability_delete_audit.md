# W8 (Hardening) — Observability: delete auditability + load-count integrity

**Status:** QUEUED (design only — no code yet; build via `/waves` W8, TDD-first).
**Raised:** 2026-07-07, during the #12 parallel-run soak review.
**Traces to:** `specs/04` (deletes, LOAD-5) · `specs/07` (logging, FR-17) · `specs/12` (audit trail,
FR-AUD-5) · `specs/10` OQ-15. Gate: `CLAUDE.md` #8 (clean-code) + #2 (tests-first).

## Why (evidence from the soak)
Two auditability gaps surfaced when reviewing the overnight sweep + evening upsert:

1. **Deleted rows are not identifiable.** The 2026-07-07 01:00 sweep deleted **8 GL rows**
   (Evergreen `gl_del=4`, Veritashs `gl_del=4`). The INFO log and `audit_daily_*.json` record only the
   **count** (`deleted: 8`); no key list, no deletes manifest, rows are hard-deleted. → the specific
   rows **cannot be validated after the fact**, and a future false-delete would be invisible.
   (Count-only logging is per `specs/07` FR-17 Make-parity; keys are a deliberate *enhancement*.)

2. **`rows_affected` is dead.** `specs/12` defines `*_rows_affected` = `cursor.rowcount` (proxy for
   inserted+updated). In every soak audit it is **0** — including the sweep that loaded 3,115 rows.
   So we can report neither the true insert/update split (OQ-15) **nor** even the total-touched proxy.

## Scope (two items)

### Item A — Delete auditability
- **FR-OBS-A1** When the windowed GL delete / AP sweep removes rows, capture each deleted natural key
  (GL: `Source_Env, Record_No, Line_No`; AP: `Source_Env, Record_No`) **before** the delete commits.
- **FR-OBS-A2** Write the keys to (a) the run's `audit_daily_*.json` under `environments[].gl_deleted_keys`
  / `ap_deleted_keys`, and (b) a rolling `logs/deletes_<date>.jsonl`.
- **FR-OBS-A3** Archive deleted rows to `test_GL_deleted_archive` / `test_AP_deleted_archive`
  (same columns + `deleted_at`, `run_id`) so a delete is reversible/inspectable. (`test_`-prefixed →
  respects `CLAUDE.md` #3 + `prod_write_guard`.)
- **Cap/perf:** keys captured via the `OUTPUT DELETED.*` clause on the existing delete, `Source_Env`-scoped
  (`specs/04` FR-LOAD-5) — no extra round-trip, no unbounded memory (bounded by rows actually deleted).

### Item B — Load-count integrity (fixes OQ-15 base case)
- **FR-OBS-B1** Capture the real per-proc rowcount. Root-cause why `cursor.rowcount` is `-1/0`
  (candidates: `SET NOCOUNT ON` in the bulk proc; multi-statement OPENJSON merge). Prefer the proc
  **OUTPUT/return a result set** `(inserted, updated)`; fall back to a post-load `MERGE ... OUTPUT`
  count if the proc can't be changed (procs are black boxes — `CLAUDE.md` #4).
- **FR-OBS-B2** Record `gl_inserted`/`gl_updated` (+ AP) in the audit when the proc exposes them;
  otherwise record a **non-zero** `rows_affected` (total touched) and mark `split_available: false`.
  Never silently emit `0` — a genuinely-zero-change run must be distinguishable from an uncaptured one.

## Tests-first (TDD — write RED before any code; `CLAUDE.md` #2)
- `test_delete_keys_captured_in_audit` — seed a `test_` row absent from a stubbed Sage pull; run sweep;
  assert the key appears in `audit.environments[*].gl_deleted_keys` **and** `test_GL_deleted_archive`.
- `test_delete_keys_source_env_scoped` — two envs share a `Record_No`; deleting env A's row must not
  capture/rm env B's (guards the `specs/04` cross-env hazard).
- `test_rows_affected_nonzero_on_real_change` — load N new rows; assert `rows_affected == N` (RED today:
  currently 0).
- `test_insert_update_split_when_proc_supports` — mixed insert+update batch; assert
  `inserted + updated == loaded` when `split_available`.
- `test_zero_change_run_distinguishable` — re-run identical batch; assert `rows_affected == 0` **and**
  `ok=True` **and** it's flagged as a true no-op, not an uncaptured metric.

## Parity / DoD
- Reconcile still green (`specs/09` AC-1..AC-3) — instrumentation must not alter loaded data.
- Zero open code-review findings (`CLAUDE.md` #8) with the gating tests above CLOSED before **S**.
- OQ-15 updated (split delivered or documented proc-limited); `specs/12` FR-AUD-5 satisfied.

## Architecture (A-step — concrete touchpoints)

Verified against the current code:

**Item A — deleted keys (BUILD NOW, pure Python, no new tables):**
- `sql_loader.py` `sweep_gl`/`sweep_ap` already compute `missing` (the exact deleted key tuples) and
  keep only `len(missing)`. Add to `LoaderMetrics`: `gl_deleted_keys: list = []`, `ap_deleted_keys:
  list = []`; append each `missing` key (as a list, JSON-safe) alongside the existing count.
- `audit.py` `EnvAudit`: add `gl_deleted_keys`/`ap_deleted_keys` (default `[]`); surfaced by `to_dict()`.
- `orchestrator.py`: copy `loader.metrics.*_deleted_keys` → `EnvAudit` (same place it copies `*_deleted`).
- New deletes log: on any sweep delete, append one JSON line per key to `deletes_{run_id}.jsonl`
  (`{run_id, env, object, key, at}`) in the audit `dir` (override via `audit.deletes_dir`), via the
  audit sink pattern — never blocks the run (FR-AUD-7). Written from `cli` after the audit trail.

**Item B — honest rows_affected (BUILD NOW, pure Python):**
- `sql_loader.py:79` `_exec_proc_json` currently does `max(0, rowcount or 0)` → clamps the proc's `-1`
  ("no rowcount") to `0`. Change: return the RAW rowcount; `record_page` treats `affected < 0` as
  **unknown** — adds 0 to the count but sets `{kind}_rows_affected_known = False`.
- `LoaderMetrics` + `EnvAudit`: add `gl_rows_affected_known`/`ap_rows_affected_known` (default `True`).
  Audit then shows `rows_affected: 0, rows_affected_known: false` on an uncaptured run vs
  `rows_affected: N, rows_affected_known: true` — satisfies FR-OBS-B2. Numeric fields stay `int` (0 when
  unknown) so all existing sums (`total_affected`, `totals()`) remain int-safe → **no parity impact**.

**DEFERRED (need DBA/proc work — flagged, NOT built this slice):**
- **A3 archive tables** `test_GL_deleted_archive`/`test_AP_deleted_archive` — require DBA to provision
  `test_` tables (a W0-style dependency). Deferred to a follow-up; A1+A2 already make deletes fully
  traceable (keys logged + in audit), so this wave is shippable without it.
- **B insert-vs-update split** (`inserted`/`updated`) — requires the bulk proc to OUTPUT counts; procs
  are black boxes (`CLAUDE.md` #4). Stays **OQ-15**. This slice fixes the *dead metric*; the split is a
  separate proc-support item. `split_available: false` is reported honestly until then.

## Tests-first matrix (RED before code)
| Test | File | Asserts (fails today) |
|---|---|---|
| `test_sweep_gl_records_deleted_keys` | `tests/unit/test_sql_loader_sweep.py` | `metrics.gl_deleted_keys == missing`; count unchanged |
| `test_sweep_source_env_scoped_keys` | same | env A delete doesn't capture env B's shared `Record_No` |
| `test_deleted_keys_flow_into_audit` | `tests/unit/test_audit.py` | `EnvAudit.to_dict()['gl_deleted_keys']` present |
| `test_rowcount_unknown_not_clamped_to_zero` | `tests/unit/test_sql_loader.py` | proc rowcount `-1` → `rows_affected_known False` (today: silently 0/True) |
| `test_rowcount_real_change_counted` | same | rowcount `N` → `rows_affected == N`, `known True` |
| `test_zero_change_distinguishable_from_unknown` | `tests/unit/test_audit.py` | genuine 0 (`known True`) ≠ uncaptured 0 (`known False`) |

## Out of scope
Alerting on false-deletes (that's OQ-9 / #8 SMTP wiring). This note only makes deletes + load-counts
**observable**; acting on them is separate. Archive tables + insert/update split are deferred (above).
