# Design — Multi-entity keying with `Source_Env` (entity-scoped load & delete)

**Status:** PROPOSED (awaiting approval before spec edits + TDD build).
**Date:** 2026-07-06. **Owner:** Indu. **Relates to:** path-to-prod #7 (multi-entity), #13
(cutover), specs/04 (load), specs/13 (delta), OQ-31 (new).

---

## 1. Problem (what the 2026-07-06 multi-entity test exposed)

The Make pipeline loads **every Sage company** (env) into the **same** two SQL tables
(`GLTable`, `APBillTable`) with **no column that identifies the source company**. Two defects
follow:

1. **AP `Record_No` collides across companies.** `RECORDNO` is Sage's per-company primary key;
   sequences restart per company, so Evergreen bill `339105` and Veritashs bill `339105` are
   *different bills with the same key*. Empirically, loading Veritashs (5,551 window bills) on top
   of Evergreen grew `APBillTable` by only **+3,210** — **2,341 Evergreen rows were silently
   overwritten** (MERGE-by-`Record_No`, last-writer-wins). 385 rows (1.5%) end with a different
   entity winner than prod: collision resolution is **load-order / timing dependent and
   non-deterministic** in both Make and our tool. GL is immune (key `Record_No + Line_No` uses
   globally-unique composite ids; 0 collisions observed).

2. **Every delete path is entity-blind.** `wipe_window` (GL window-DELETE + AP full-wipe) and the
   delta `sweep_gl`/`sweep_ap` operate on the whole table with no company filter. So a delta run
   for **one** env would treat the **other** env's rows as "missing" and delete them (or trip the
   mass-delete guard and abort). One env's run cannot currently be isolated from another's.

**Uniqueness verified (2026-07-06):** `RECORDNO` is unique *within* each company — Evergreen
20,834/20,834 distinct, Veritashs 2,204/2,204 distinct, 0 in-company duplicates (plus the Sage
structural guarantee). `Record_ID` (document number, e.g. `"Placker Charles REFUND"`,
`"AG-01-APR-26"`) is **not** unique and must **not** be used as a key.

---

## 2. Decision (approved 2026-07-06)

Add an explicit company discriminator and key/scope everything by it.

| Item | Decision |
|---|---|
| **Discriminator** | New column **`Source_Env`** (the config env `name`, e.g. `Evergreen`/`Veritashs`) on `test_GLTable` and `test_APBillTable`, stamped at load. |
| **AP primary key** | **`(Source_Env, Record_No)`** — both entities coexist; deterministic; no overwrite. |
| **GL key** | Natural key stays `(Record_No, Line_No)` (globally unique). `Source_Env` added as a column for delete-scoping + provenance. |
| **All deletes** | `wipe_window` (GL + AP) and `sweep_gl`/`sweep_ap` gain `AND Source_Env = ?` so a run only ever touches its own env's rows. |

**Rejected alternatives:** `Location_ID` as discriminator (NULLs; one company spans many
locations) — unreliable. `Record_ID` in the key — not unique.

**Accepted divergence from Make:** with AP keyed by `(Source_Env, Record_No)` the table holds
**~27,784** rows (Evergreen 22,233 + Veritashs 5,551) vs prod's collision-collapsed **25,443**.
This is a deliberate **correctness fix** — prod's 25,443 is a lossy, non-deterministic artifact.
Any downstream consumer of `APBillTable` must be able to handle the entity dimension.

---

## 3. Changes by layer

### 3.1 Schema / DDL (`test_` tables — we own these; reference DDL under `reference/sql/`)
- `test_GLTable`: `ADD Source_Env NVARCHAR(64) NOT NULL`. Keep unique `(Record_No, Line_No)`.
  (Add `Source_Env` to any index used by the sweep window-scan for performance.)
- `test_APBillTable`: `ADD Source_Env NVARCHAR(64) NOT NULL`; **drop PK on `Record_No`**, create
  **PK `(Source_Env, Record_No)`**.
- Existing rows: back-stamp `Source_Env='Evergreen'` where NULL before applying NOT NULL, OR
  truncate-and-reload both envs (cleaner — the current `test_APBillTable` is already collision-
  polluted from the additive test).

### 3.2 Stored procedures (`test_` procs — mirror of prod; **this is the black-box exception**)
CLAUDE.md rule #4 treats procs as opaque, but the merge/insert key lives *inside* them, so the
key change is unavoidable:
- `test_AddAPBillBulk`: accept `@Source_Env` (or read a `Source_Env` field in the page JSON) and
  **MERGE on `(Source_Env, Record_No)`** instead of `Record_No`. Writes `Source_Env` to the column.
- `test_AddGLDetailBulk`: write `Source_Env` (INSERT unchanged otherwise; key already unique).
- `test_SyncAPLinksToGL`: the join `GL.Bill_No = AP.Record_ID` must also match `Source_Env`
  (`... AND GL.Source_Env = AP.Source_Env`) — otherwise, with two entities present and `Record_ID`
  non-unique, it can stamp a GL row from the **wrong** company's bill. **This is the #5 risk from
  the plan and is the subtlest part of the change — must be covered by a test.**
- `test_UpdateVendorInfoInGL`: same `Source_Env`-aware scoping.

> Prod cutover ripple (OQ-31): the same column + merge-key + join changes are required on the
> **prod** tables/procs, coordinated with the DBA and with every downstream reader of these tables.

### 3.3 Loader (`sql_loader.py`)
- `load_gl_records` / `load_ap_records`: pass the current `Source_Env` through to the proc call.
- `wipe_window`: `... WHERE {gl_delete_date_field} BETWEEN ? AND ? AND Source_Env = ?`; AP full
  reload → `DELETE FROM {ap_table} WHERE Source_Env = ?` (no longer whole-table).
- `sweep_gl` / `sweep_ap`: scope the window key-read **and** the delete by `Source_Env` so a run
  only compares/deletes its own env. Mass-delete guard unchanged (defense-in-depth).
- `SqlLoader` gains a `source_env` (constructor arg or per-call param).

### 3.4 Orchestrator
- Per-env loop already knows `env["name"]` → pass it as `source_env` to the loader for every
  wipe / load / reconcile / sweep call. No cross-env change to isolation logic.

### 3.5 Config
- No new keys required (env `name` is the `Source_Env`). Optionally `sql.source_env_column` (default
  `Source_Env`) for name-configurability, consistent with the "nothing hardcoded" rule.

---

## 4. Test plan (TDD — failing tests first, per CLAUDE.md #2)

1. **AP coexistence:** load env A then env B with colliding `Record_No`; assert **both** rows
   present (count = A+B), each with its own `Source_Env`. (Red today: B overwrites A.)
2. **Delete isolation — wipe:** `wipe_window(env=A)` deletes only A's rows; B untouched.
3. **Delete isolation — sweep:** `sweep_gl`/`sweep_ap` for env A never deletes B's rows even when
   B's keys are absent from A's key set.
4. **Link join correctness:** two envs share a `Record_ID` value; `SyncAPLinksToGL` stamps each
   GL row from its **own** env's bill (no cross-env leakage). (Red today.)
5. **Regression:** single-env behavior (Evergreen-only) unchanged — counts, parity, watermark.
6. **Reconciliation (offline):** existing dry-run parity still green with `Source_Env` added.

---

## 5. Rollout

1. Approve this design → apply spec edits (§6) → write failing tests (§4) → implement.
2. Rebuild `test_` DDL + `test_` procs with `Source_Env`; truncate + reload both envs cleanly
   (Evergreen + Veritashs, Apr–Jul) via the orchestrator.
3. Verify: `test_APBillTable` ≈ 27,784 (22,233 + 5,551), zero cross-env overwrite; run an
   Evergreen-only delta and confirm Veritashs rows are untouched.
4. **Prod cutover (separate, gated):** OQ-31 — prod DDL + proc changes + downstream sign-off.

## 6. Proposed spec edits (apply on approval)
- **04_sql_load.md** — new *FR-LOAD-7 Multi-entity keying*: `Source_Env` column; AP key
  `(Source_Env, Record_No)`; all deletes/sweeps `Source_Env`-scoped; `SyncAPLinksToGL` join adds
  `Source_Env`. Note the divergence from Make (row count) and the black-box-proc exception.
- **13_incremental_delta_load.md** — §13.5 (delete-sweep): sweeps are `Source_Env`-scoped;
  cross-env rows are never candidates for deletion.
- **10_open_questions.md** — add **OQ-31** (below).

## 7. Open questions (OQ-31)
- **Prod schema/proc change:** who owns adding `Source_Env` + changing the AP merge key + the
  `SyncAPLinksToGL` join on the **prod** tables/procs (DBA), and the change window?
- **Downstream consumers:** which reports/apps read `APBillTable`/`GLTable`, and can they handle
  (a) the new column and (b) the AP row-count increase (25,443 → ~27,784)?
- **Full entity list:** confirmed Evergreen + Veritashs here; enumerate **all** Sage companies Make
  loads so every one is configured (only these two are enabled now).
- **Back-population:** do prod tables need historical `Source_Env` back-fill, or is it forward-only
  from cutover?
