# 11 — Test & Reconciliation Protocol

How we prove the Python utility is correct: **automated parity** of the new utility's output against
Make's output, for the same `(environment, window)`, with **strict production isolation**. Stack:
`pytest` + `pyodbc`. Aligns with `09_acceptance_criteria.md`.

## 11.1 Golden rule — production isolation

- **Make writes PROD** tables on its live schedule: `Intacct.dbo.GLTable`, `Intacct.dbo.APBillTable`
  (enriched by the 4 procs). These are **read-only** to us — the reference ("golden") output.
- **The utility writes `test_` targets only**: `test_GLTable`, `test_APBillTable`, via `test_` proc
  variants (`test_AddGLDetailBulk`, `test_AddAPBillBulk`, `test_SyncAPLinksToGL`,
  `test_UpdateVendorInfoInGL`) that are identical to the prod procs except they write the `test_`
  tables. Procs stay **black box**; only their target table name differs.
- The two run **in parallel** against the same Sage data; we compare `test_*` vs prod for the same
  window. We **never** write prod (enforced by `prod_write_guard` hook + `test_`-only test config).

> **W0 / DBA deliverable (OQ-1, OQ-11):** provision the `test_` tables (same DDL as prod) and the 4
> `test_` proc variants. Without these, parity cannot run.

## 11.2 TDD flow (locked — see CLAUDE.md / WAVES `V`)

For every wave and every change: **write the test first → red → implement → green.** Test files are
committed before or with the code. A reconciliation test for a wave is authored from the spec's
expected behavior and fails until the wave's code exists.

## 11.3 Test tiers

1. **Unit tests** (`tests/unit/`) — pure logic, no DB/Sage. e.g. run_type/window derivation
   (`05 §5.2/5.5` — edge cases: 1st of month, mid-month, year boundary, 28/30/31-day months),
   month-chunk slicing, page-count math (GL `numremaining+1`, AP `totalcount`, `ceil(n/2000)`),
   Sage XML query builder (exact fields/filter/orderby vs `02`), JSON-page encoding. Fast, run always.

2. **Component/integration tests** (`tests/integration/`) — against `test_` DB and/or a Sage sandbox or
   recorded fixtures. e.g. a page loads into `test_GLTable` via `test_AddGLDetailBulk`; transaction
   rollback on injected failure leaves `test_` tables unchanged (AC-9).

3. **Reconciliation tests** (`tests/reconciliation/`) — the parity gate. Compare `test_*` (utility) vs
   prod (Make) for a fixed fixture window. This is the wave's **Evaluate** gate.

## 11.4 Reconciliation metrics (the parity gate)

For a fixture `(env, start, end)`, compare utility `test_*` vs Make prod, scoped to the window:

| Check | Method | Pass |
|---|---|---|
| **Row count** (AC-1) | `COUNT(*)` per table, windowed | exact match |
| **Column checksum** (AC-2) | `CHECKSUM_AGG`/`HASHBYTES` over ordered key cols per table | exact match |
| **Sample rows** | N random/keyed rows full-column diff | zero diffs |
| **Enriched cols** (AC-3) | GL `Stampli_Link`, `Platform_Services_Link`, vendor id/name post-reconcile | exact match |
| **Filter fidelity** (AC-4) | only `BOOKID='ACCRUAL'` GL in window; AP in window | spot queries pass |
| **Pagination** (AC-5) | total loaded == Sage `totalcount` | exact (window >2000 rows) |

A reconciliation test **fails the wave** on any mismatch and prints the diff (counts, mismatched keys,
sample rows) for triage. Likely first-diff culprits: date-field mismatch (OQ-2), AP probe-vs-page
field set, JSON key casing, proc/table mapping.

## 11.5 Fixtures

- A small, stable `(env, single-month window)` for W1–W3 parity (comparable as scope grows).
- A `>2000-row` window for W4 pagination.
- A multi-month window for W5 orchestration; all envs for W6; a 1st-of-month full reload for W7.
- Fixtures + expected metrics recorded under `tests/fixtures/` (no secrets; PII-safe — counts/checksums,
  not raw financial rows in VCS).

## 11.6 Snapshot timing (avoid moving targets)

Make refreshes prod on a schedule; to compare apples-to-apples:
- Snapshot Make's prod metrics for the fixture window (read-only) at a known time, **then** run the
  utility for the same window into `test_*`, then compare. Re-snapshot if Make re-ran in between.
- Where feasible, drive both from the **same Sage extract window** so source data is identical.

## 11.7 CI / local execution

- `pytest tests/unit` — always, fast, no external deps (gate for every commit).
- `pytest tests/integration -m integration` — needs `test_` DB / Sage access.
- `pytest tests/reconciliation -m reconciliation` — the wave Evaluate gate; needs `test_` DB + Make prod read.
- Markers keep DB-bound tests out of the fast loop. Connection params from test config (env-scoped),
  secrets from `.env`.

## 11.8 What "green" authorizes
Passing reconciliation for a wave's fixture authorizes **Ship** of that wave (WAVES `S`). Passing the
full fixture matrix (W1–W7) + hardening (W8) authorizes **production cutover** (`09 §9.3`): Make set
inactive, retained for rollback.
