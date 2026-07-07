# WAVES — Spec-Driven Development Process for `intacct_sql_etl`

This project is built **spec-first** and shipped in **incremental, parity-gated vertical slices**.
Each slice is a **wave**. Every wave runs the same five-step **WAVES** cycle.

> **Single source of truth = `specs/`.** Code, tests, and designs must trace to a requirement in
> `specs/`. If reality disagrees with the spec, **fix the spec first**, then the code — never the
> other way around. The Make blueprints in `reference/` are the *origin* of the spec, but the spec is
> what we build to.

---

## The WAVES cycle (run once per wave)

| Step | Name | Phase | Gate to advance |
|---|---|---|---|
| **W** | **Walk the spec** | Discovery | Wave's scope + Definition of Done written, tracing to specific FR/AC ids. No invented requirements; any gap is resolved by updating `specs/` first. |
| **A** | **Architect** | Design | Component/interface/SQL design recorded in `docs/design/W<n>_*.md`; affected open questions (`specs/10`) resolved or explicitly deferred. **User design-check.** |
| **V** | **Verify-first build** | Dev (TDD, locked) | **Tests FIRST, always.** Write reconciliation + unit tests from the spec, get them agreed, see them FAIL (red); then implement to green. Tests committed before/with code, never after. No prod code without a failing test that justified it. Small commits. Prod-write guard active. |
| **E** | **Evaluate parity** | Test | New utility run into `test_`-prefixed tables, **in parallel** with Make's output, then reconciled (row-count + checksum + sample diff). **Parity must pass** (see `specs/11`). |
| **S** | **Ship** | Release | **Code review run + recorded in `docs/CODE_REVIEW.md` with ZERO open findings** (every finding CLOSED with a gating test); increment committed/tagged; WAVES status + `specs/08` traceability updated; demo to user; Make left live for rollback. Promote to next wave. |

**Sequencing law:** `W → A → V → E → S`, strictly. A wave does not enter **V** until **W+A** are
accepted; it does not **S** until **E** (parity) is green. No skipping; no code before tests; no
release before parity.

---

## Principles

1. **Spec is law.** Build only what `specs/` says. Gaps → update spec (with user) before coding.
2. **Black-box the SQL procs.** `AddGLDetailBulk`, `AddAPBillBulk`, `SyncAPLinksToGL`,
   `UpdateVendorInfoInGL` are treated as opaque contracts (inputs/effects only — see `specs/04`).
   We call them; we do not reimplement or inspect their internals. Parity is judged on their *output*.
3. **Parity is the definition of correct.** "Done" = the new utility reproduces the Make result for
   the same window (`specs/09` AC-1..AC-6). Reconciliation is automated, not eyeballed.
4. **Never touch production tables in dev/test.** All test runs target `test_`-prefixed tables/procs
   and run **in parallel** with Make. Enforced by the `prod_write_guard` hook (hard-block) **and** by
   config (test config only names `test_` targets). See `specs/11` + `CLAUDE.md`.
5. **Incremental & shippable.** Vertical thin slices: each wave is end-to-end (extract→load→reconcile)
   for a narrow scope, independently parity-tested. No big-bang.
6. **Reversible.** Make stays live and untouched until final cutover; every wave is rollback-safe.
7. **Crash-safety by design.** Window refresh is transactional / staging-swap (`specs/04` FR-LOAD-5) —
   an improvement over Make's naive delete-then-insert, validated in the hardening wave.
8. **Everything configurable.** No hardcoded connections, creds, envs, filters, sizes, schedules
   (`specs/06`).
9. **Clean-code gate.** No wave ships with an open code-review finding. A SonarQube/Sentry-style review
   (logging, exception handling, resource management, dead code, naming, OOP/DRY, security/injection) is
   recorded in `docs/CODE_REVIEW.md`; every finding is CLOSED with a gating test (TDD) before **S**.

---

## Tech stack (locked)

- **Language/runtime:** Python 3.
- **DB access:** `pyodbc` (matches the PowerShell/SqlClient POC path).
- **Tests:** `pytest` (unit + reconciliation/integration).
- **Config:** `PyYAML` (+ `.env`/secret store for secrets). Stdlib elsewhere; `requests` permitted for Sage HTTP.
- Dependencies pinned in `requirements.txt` at the first dev wave (W0).

---

## Wave plan (vertical slices)

Each wave = one WAVES cycle. Scope grows; mechanism is reused.

| Wave | Scope (vertical slice) | Primary spec refs | Parity target |
|---|---|---|---|
| **W0** Foundations | Scaffolding, `requirements.txt`, config+secrets loader, Sage + SQL connectivity smoke test, **provision `test_` tables + `test_` proc variants** (DBA), reconciliation harness skeleton, prod-write guard wired. | 06, 07, 11 | Connectivity green; harness runs (no data parity yet). |
| **W1** GL · 1 env · 1 month | Extract `GLDETAIL` (single month, single env) → `test_GLTable` via `test_AddGLDetailBulk` → reconcile raw GL load vs Make `GLTable`. | 02, 04(LOAD-2), 09 | AC-1/AC-2 on GL rows (pre-enrichment). |
| **W2** AP · 1 env · 1 month | Extract `APBILL` → `test_APBillTable` → reconcile vs Make `APBillTable`. | 02, 04(LOAD-2) | AC-1/AC-2 on AP rows. |
| **W3** Reconcile procs | Run `test_SyncAPLinksToGL` + `test_UpdateVendorInfoInGL` → reconcile enriched GL cols (links, vendor). | 04(LOAD-3), 03(TR-6) | AC-3. |
| **W4** Pagination at scale | A window with >2000 rows (multi-page) for GL + AP. | 02(EXT-4), 09 | AC-5 (no page lost). |
| **W5** Orchestration | run_type + window derivation + month-chunk slicing + windowed GL delete / full AP wipe + transaction/staging strategy; full daily window (multi-month). | 05, 04(LOAD-1/5) | AC-7, AC-8, AC-9 (crash-safety). |
| **W6** Multi-environment | All configured envs, per-env creds, failure isolation. | 06, 07(7.5) | AC-6. |
| **W7** Monthly full reload | 1st-of-month path (Jan-1-last-year → now, large). | 05 | AC-1/AC-2 at full scale. |
| **W8** Hardening & release | Retries/backoff, structured logging + alerting, scheduling, runbook, cutover plan. | 07, 09 | AC-10..AC-13; **production cutover** (Make → inactive, retained for rollback). |

> Waves W1–W3 may share the same fixed `(env, window)` fixture so parity is comparable as scope grows.

---

## Definition of Ready (to start a wave's **V**)
- [ ] Scope + DoD written, tracing to FR/AC ids (**W** done).
- [ ] Design recorded + user-checked (**A** done).
- [ ] Required open questions for this wave resolved in `specs/10` (or explicitly deferred).
- [ ] `test_` targets for this wave exist (tables/procs provisioned).

## Definition of Done (to **S** a wave)
- [ ] Tests written before code; all green.
- [ ] Reconciliation (`specs/11`) **passes** for the wave's fixture (counts + checksums + sample).
- [ ] No production-table writes occurred (guard clean; test config only).
- [ ] **Code review run; `docs/CODE_REVIEW.md` shows ZERO open findings** (each CLOSED with a gating test).
- [ ] `specs/08` traceability + this file's status table updated.
- [ ] Committed on a branch; demoed; Make untouched.

---

## Roles

- **You (user / DBA):** approve spec changes + designs; provision `test_` tables/procs + SQL creds
  (the POC blocker, `Memory_Opti.txt` §8); confirm parity results; authorize cutover.
- **Claude:** drive each WAVES step, keep the spec the source of truth, write tests-first, run
  reconciliation, never write prod, surface decisions as open questions.

## How to run a wave
Use the **`/waves`** skill (e.g. `/waves W1`) — it walks the five steps in order and enforces the
gates. Status is tracked in the wave-plan table above.

## Status
- [x] Spec authored (`specs/00`–`11`).
- [x] WAVES process + guardrails established (this file, `CLAUDE.md`, `prod_write_guard` hook, `/waves` skill).
- [x] **W0 — Foundations** — scaffolding, `pytest.ini`, `requirements.txt`, `config`/`dates`/`pagination` modules. 42 unit tests green. (Live connectivity check deferred to creds, OQ-11.)
- [x] **W1 — GL slice** — `sage_query`/`sage_client`/`transform`/`sql_loader`/`reconcile`/`orchestrator` (GL extract→transform→load→**dry-run parity**). Design: `docs/design/W1_design.md`. Green incl. dry-run parity gate + drift counter-test.
- [x] **W2 — AP slice** — AP query builder (4-field probe / 7-field page, `showprivate`, `totalcount` basis) + `fetch_ap` + `load_ap_records`. Green. **79 tests total.**
- [x] **W3 — Reconcile + wipe** — `wipe_window` (windowed GL / full AP, configurable) + `run_reconcile`
      (configurable procs, bound date params). Reconcile granularity is a **config knob** (OQ-4).
- [x] **W5 — Orchestration** — `orchestrator.run()`: run_type/window derivation + overrides, month
      slicing, per-env loop, table wipe, per-chunk GL+AP extract/load, reconcile, **per-env transaction
      (commit/rollback)**, failure isolation. Crash-safety verified via rollback test (AC-9 offline).
- [x] **W6 — Multi-environment** — per-env creds (`resolve_env_creds`, secret-store pattern), isolation.
- [x] **Config-driven adapters** — `sage_transport` (parsers + `xml_to_dict` + http factory),
      `db` (connection-string factory, all auth modes), `logging_setup` (configurable run-log location),
      `cli` (entrypoint). **114 tests green.**
- [ ] **W4 — Pagination at scale** — covered in logic/unit; needs a live >2000-row window for AC-5.
- [ ] **W7 — Monthly full reload** — logic covered (derivation + slicing tested); needs live scale run.
- [ ] **W8 — Hardening + cutover** — retries/backoff, `staging_swap` strategy, scheduling, SharePoint
      log option, live parity vs Make, runbook. **Blocked on creds/DBA (OQ-1, OQ-11, OQ-14).**
   - [x] **W8·observability increment (2026-07-07)** — delete-sweep now records WHICH keys were removed
         (audit JSON + `deletes_{run_id}.jsonl`); `rows_affected` no longer clamps the proc's `-1` to a
         silent `0` (honest `*_rows_affected_known` flag). 9 new tests, **270 green**, review pass 4
         CLOSED. Deferred: A3 archive tables (DBA), insert/update split (OQ-15). See
         `docs/design/W8_observability_delete_audit.md`.

> **Offline-tested:** all pure logic + full extract→transform→load→reconcile orchestration via injected
> fakes/mocks, response parsers, connection-string assembly, run-log, CLI, and dry-run parity.
> **Not yet exercised live** (needs SQL creds + `test_` tables/procs + Sage access — OQ-11, OQ-1):
> real HTTP call + live wire-shape validation (OQ-14), real proc execution, live parity vs Make.
> `staging_swap` load strategy is recognized but raises until provisioned (default `transaction`).
> No production code path can write prod tables in test_mode (config guard + prod_write_guard hook).
