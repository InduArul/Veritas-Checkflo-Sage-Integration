---
name: waves
description: Drive one increment ("wave") of the intacct_sql_etl project through the WAVES spec-driven, TDD, parity-gated cycle (Walk spec → Architect → Verify-first build → Evaluate parity → Ship). Use when the user says "start wave N", "next wave", "/waves", "run the WAVES cycle", "advance the Intacct→SQL ETL build", or wants to design/build/test/release any increment of intacct_sql_etl. Enforces: spec is source of truth, tests-first (TDD locked), test_-prefixed tables only, parity reconciliation vs Make before ship.
---

# WAVES — drive one wave of `intacct_sql_etl`

Read `intacct_sql_etl/CLAUDE.md`, `intacct_sql_etl/WAVES.md`, and the relevant `intacct_sql_etl/specs/*`
before acting. The argument is the wave id (e.g. `W1`); if none given, pick the next unstarted wave
from the WAVES.md status/plan table and confirm with the user.

**Hard rules (binding — from CLAUDE.md):**
- Spec (`specs/`) is the single source of truth. Found a gap? Update the spec WITH the user first, then proceed.
- TDD is locked: write tests FIRST, see them fail (red), then code to green. Never code before tests.
- NEVER write production tables/procs (`GLTable`, `APBillTable`, the 4 procs). Dev/test → `test_`-prefixed only, in parallel with Make. (Enforced by the prod_write_guard hook.)
- SQL procs are black boxes — call, don't reimplement. Correctness = parity, not inspection.
- Make stays live until final cutover. Work on a branch; don't push/cutover without approval.

Run the five steps **in order**. Do not advance a step until its gate is met. Announce each step.

## W — Walk the spec (Discovery)
1. State the wave's scope (from the WAVES.md plan) and write its **Definition of Done**, tracing to
   specific FR/AC ids in `specs/`.
2. List the open questions in `specs/10` that block this wave. If any are unresolved and blocking,
   surface them to the user and pause — do not guess.
3. Confirm the `test_` targets this wave needs exist (tables/procs); if not, flag as a DBA/W0 dependency.
   **Gate:** scope + DoD agreed; blocking OQs resolved or explicitly deferred.

## A — Architect (Design)
1. Write `intacct_sql_etl/docs/design/<wave>_design.md`: components/interfaces/SQL for the slice,
   data flow, config keys used, decisions, and how it traces to the spec.
2. Note any spec/open-question updates this design implies.
   **Gate:** user design-check (present the design, get an explicit OK).

## V — Verify-first build (Dev, TDD locked)
1. Write the tests FIRST (`tests/unit/`, `tests/integration/`, `tests/reconciliation/` as appropriate),
   derived from the spec's expected behavior. Run them — they MUST fail (red). Show the user.
2. Implement `src/` minimally to make them pass (green). Small commits on a branch. Pin deps in
   `requirements.txt`. Keep secrets out of source.
   **Gate:** all wave tests green; prod-write guard clean (no prod writes attempted).

## E — Evaluate parity (Test)
1. Run the utility for the wave's fixture `(env, window)` into `test_`-prefixed tables, in parallel
   with Make's prod output.
2. Run the reconciliation per `specs/11` (row counts + column checksums + sample diff + enriched cols).
   Print results.
   **Gate:** parity PASSES. On any mismatch, triage (likely: date-field OQ-2, AP probe-vs-page fields,
   JSON key casing, proc/table mapping), fix via the TDD loop, re-run. Never weaken a test to pass.

## S — Ship (Release)
1. **Run the code review** (SonarQube/Sentry-style: logging, exception handling, resource management,
   dead code, naming, OOP/DRY, security/injection). Record it in `docs/CODE_REVIEW.md`. **Every finding
   must be CLOSED (✅) with a gating test (fix via the TDD loop) — ZERO open findings, or the wave does
   NOT ship.** (Binding gate: CLAUDE.md non-negotiable #8.)
2. Update `specs/08` traceability + the `WAVES.md` status table (mark wave done).
3. Commit/tag the increment on its branch; write a short demo/summary for the user.
4. Confirm Make untouched + rollback path intact. Propose the next wave.
   **Gate:** code review clean (0 open findings); user acknowledges; do NOT cut over to production until
   the final hardening wave (W8) and explicit user authorization.

End by reporting which step completed, the gate result, and the next action.
