# Make Structural Refactor — QA Checklist (v1.0)

**Purpose:** Standard gate for any Make scenario refactor whose intent is **structural only**
(consolidation, re-wiring, de-duplication) with **0% functional change**.

**Golden rule:** Every parameter, input, output, mapping, filter, connection, and setting must be
**identical in behavior**. If anything changes that a downstream system or a user could observe,
it is OUT of scope for a structural refactor.

> **Canonical regression to prevent (the `limit=10` incident):** a field left **blank** in the
> original (meaning "no limit / return all rows") must never be re-created with Make's **UI default**
> (e.g. `limit: 10`). A blank that becomes a default is a *functional* change disguised as structural.
> This is Control #1 below and is enforced by `tools/parity_diff.py`.

**Testing model:** Static testing (pre-deploy, no execution) **and** live testing in a **Staging**
environment before any production deploy.

---

## 0. Baseline capture (BEFORE touching anything)
- [ ] Export and archive every affected scenario blueprint (pin version + timestamp + git commit).
- [ ] Record live ops/exec and exec count per scenario (the "before" number we must beat).
- [ ] Capture a **golden reference run**: full input webhook payload, datastore state, Excel log +
      tracker rows, Sage request/response XML, reporting email, and final `WebhookRespond` bodies for
      one known batch.
- [ ] Snapshot all scenario-level settings (scheduling, sequential, dataloss, maxErrors, autoCommit,
      roundtrips, freshVariables, DLQ).

## 1. 🔴 Parameter-parity controls (0% functional change)
- [ ] **Blank-vs-default trap.** Every field left empty in the original stays empty in the refactor.
      NEVER substitute a Make UI default. Audit specifically:
  - [ ] List/Search **Limit** / "Maximum number of results" (the `limit=10` bug) — must stay unset.
  - [ ] Pagination / "Continue when there are more results."
  - [ ] Date-range, sort order, "Return wrapped output," "Insert missing record (upsert)," "Overwrite arrays."
  - [ ] On update/upsert modules: intentionally **omitted** fields stay omitted (never written as `""`).
- [ ] Every retained module keeps an **identical `mapper`** (same references, literals, IML, backticks).
- [ ] Every retained module keeps **identical `parameters`** (connection IDs, mode, select/map choices).
- [ ] **Filter parity:** each filter's label, operator, value, case-sensitivity, AND/OR grouping preserved.
- [ ] **Iterator/Feeder & Aggregator parity:** same feeder source, aggregator target, group-by, `stopIfEmpty`.
- [ ] **Connection parity:** same connection IDs and scopes (no silent connection swap).
- [ ] **Error-handling parity:** per-module directives (Break/Ignore/Resume/Commit/Rollback), `maxErrors`,
      DLQ, `autoCommit`, `autoCommitTriggerLast`.
- [ ] **Scheduling parity:** type, `interval`, `restrict` days/times, `maxResults`.
- [ ] **Output-contract parity:** every `WebhookRespond` / final output has the same JSON shape,
      field names, and types.

## 2. Refactor-specific integrity (Leak A — webhook fan-out consolidation)
- [ ] **Execution order preserved:** merged flow runs stages in the same sequence and respects every
      data dependency (Vendor → Negative → Invoice loop → Posting → Reporting).
- [ ] **State migration is value-exact:** anything that previously crossed via datastore/HTTP
      (`Total_value_of_checks_from_checkflo`, `value_of_payment_posted_for_a_batch`, `Email_Text`,
      indicators) now carried in-bundle with **same value AND data type** (watch number↔string coercion).
- [ ] **No orphan callers:** confirm nothing else still calls a removed webhook endpoint.
- [ ] **Idempotency (financial-critical):** no path can double-post or double-approve a Sage AP Payment.
- [ ] **Timeout/retry behavior** of former HTTP hops is replicated or proven irrelevant.
- [ ] **Loop cardinality unchanged:** same invoice/check iteration counts → same Sage calls + log rows.

## 3. Static testing (pre-deploy, no execution)
- [ ] **Automated blueprint diff:** run `tools/parity_diff.py baseline.json refactored.json` — must
      report **no functional-affecting diffs** (this is the automated guard against the `limit=10` class).
- [ ] `validate_module_configuration` on every module; `validate_blueprint_schema` on the full blueprint.
- [ ] Connection + scope verification via `extract_blueprint_components`.
- [ ] Mapping resolution review: no `{{ }}` reference resolves to undefined/null where the original had data.
- [ ] Peer review + written sign-off on the diff.

## 4. Live testing — Staging environment
- [ ] Run in a **Staging clone** pointed at non-production Sage/SharePoint targets (or sandbox), never prod.
- [ ] **Parallel run:** push the golden batch through OLD and NEW; compare field-by-field:
  - [ ] Excel **log file** content (line-for-line).
  - [ ] **Batch tracker** rows (values + row positions).
  - [ ] **Sage** request payloads + responses (AP Bill, Vendor, AP Payment XML).
  - [ ] **Reporting email** (recipients, subject success/failure logic, body, log link).
  - [ ] **Datastore end-state** and `WebhookRespond` bodies.
- [ ] **Edge cases:** zero invoices; negative checks; APBill-not-found 3-call cascade; >15 batches;
      multi-invoice check; variance mismatch (amount ≠ total); every error branch.
- [ ] **Negative tests:** force Sage auth failure, missing bank mapping, >1 vendor match — confirm same
      error routing + same log/email output.
- [ ] **Volume/load test:** a full-size batch to surface Excel lock contention, rate limits, timeouts.
- [ ] **Operations delta:** record ops/check OLD vs NEW — confirm reduction is real and behavior-neutral.

## 5. Sign-off & deployment
- [ ] Static + staging results documented; all diffs explained and approved.
- [ ] Old scenarios kept **inactive-but-intact** for one-click rollback.
- [ ] Defined deploy window + post-go-live monitoring (first N live batches watched).
- [ ] Baseline blueprints + golden run archived in the repo.

---

### Severity legend for `parity_diff.py`
- **HIGH** — a parameter/mapper/filter value changed, or a key appeared/disappeared (includes the
  blank→default trap). Blocks deploy.
- **MEDIUM** — module added/removed without an explained mapping. Requires sign-off.
- **INFO** — cosmetic only (designer x/y position, labels). Non-blocking.
