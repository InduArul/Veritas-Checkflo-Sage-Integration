# CLAUDE.md — `intacct_sql_etl` operating rules (READ FIRST)

Project: Python rebuild of the Make "Intaact feeds to SQL" family (Sage Intacct → SQL Server ETL).
This file governs how work is done in `intacct_sql_etl/`. It is binding.

## Non-negotiables

1. **Spec is the single source of truth.** Build only what `specs/00`–`11` define. If code and spec
   disagree, the spec wins — update `specs/` (with the user) *before* changing code. Never silently
   diverge. Make blueprints in `reference/` seeded the spec; the spec is what we build to.

2. **Follow the WAVES cycle** (`WAVES.md`): `Walk spec → Architect → Verify-first build →
   Evaluate parity → Ship`, strictly in order. No release before parity. Work in vertical slices
   (waves). Use `/waves <id>` to drive a wave.

   **TDD is mandatory and locked.** For ANY code change (new feature, fix, refactor): write the
   test(s) FIRST, get them reviewed/agreed, see them FAIL (red), and only then write code to make
   them pass (green). Tests are committed before or with the code, never after. No production code is
   written or merged without a failing test that justified it. This applies to every wave and every
   subsequent change — not just initial build.

3. **NEVER write to production tables/procs in dev or test.**
   - Production targets (FORBIDDEN to write in dev/test): `Intacct.dbo.GLTable`,
     `Intacct.dbo.APBillTable`, and the 4 procs `AddGLDetailBulk`, `AddAPBillBulk`,
     `SyncAPLinksToGL`, `UpdateVendorInfoInGL`.
   - All dev/test writes go to **`test_`-prefixed** tables/procs (e.g. `test_GLTable`,
     `test_AddGLDetailBulk`) and run **in parallel** with Make.
   - Reads of production tables are allowed (we snapshot Make's output to compare).
   - This is enforced by the `prod_write_guard` PreToolUse hook (hard-block on Bash/PowerShell SQL).
     Override only with a deliberate `ALLOW_PROD_WRITE` token, and only with explicit user approval —
     effectively never during this project.

4. **SQL stored procedures are BLACK BOXES.** Treat the 4 procs as opaque contracts (inputs/effects
   per `specs/04`). Call them; do not reimplement or depend on their internals. Judge correctness by
   their *output* via reconciliation.

5. **Parity defines correctness.** A wave is done only when the new utility reproduces the Make result
   for the same `(env, window)` — automated reconciliation (`specs/11`): row counts + column checksums
   + sample-row diff. Eyeballing is not parity.

6. **Reversible & isolated.** Make stays live and untouched until final cutover (W8). Every wave is
   rollback-safe. A failure in one environment must not corrupt another.

7. **Secrets** never in source, committed config, or logs. Real config is `config.yaml` (git-ignored);
   only `config.example.yaml` is committed. Secrets come from `.env`/secret store. Never log raw Sage
   request XML — it carries credentials; use `sage_query.redact()` if a request must be logged.

8. **No open code-review finding ships.** Before any release/cutover, a static code review (SonarQube/
   Sentry-style: logging, exception handling, resource management, dead code, naming, OOP/DRY,
   security/injection) is run and recorded in `docs/CODE_REVIEW.md`. **Every finding must be CLOSED
   (✅) with a gating test before Ship.** This is a hard gate in the WAVES **S** step — a wave with any
   open finding cannot be released. Fixes follow the TDD rule in #2 (failing test first). Re-run the
   review whenever code changes materially.

## Tech stack (locked)
Python 3 · `pyodbc` · `pytest` · `PyYAML` (+ `requests` for Sage HTTP). Pin in `requirements.txt`.

## Layout
- `specs/` — requirements (source of truth). `WAVES.md` — process. `CLAUDE.md` — this file.
- `reference/` — sanitized Make blueprints + lossless extractions + `sql/` (proc/DDL capture).
- `config/config.example.yaml` — config template. `src/` — code (after sign-off). `tests/` — unit + reconciliation.
- `docs/design/` — per-wave design notes.

## Workflow expectations
- Start any build work via `/waves <id>`; respect the gates in `WAVES.md`.
- When you hit an unknown, add/update an entry in `specs/10_open_questions.md` rather than guessing.
- Keep `specs/08_make_reference_map.md` (traceability) and the `WAVES.md` status table current.
- Tests-first: write the failing reconciliation/unit test, then implement.
- Commit small, on a branch; do not push or cut over without user approval.
