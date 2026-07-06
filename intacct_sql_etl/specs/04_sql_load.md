# 04 — SQL Server Load (Worker + Mainflow)

**Target DB:** `Intacct` on SQL Server (Make connection id `2803828`, "My Microsoft SQL Server
connection"). Tables: `Intacct.dbo.GLTable`, `Intacct.dbo.APBillTable`.

The Make layer never writes columns directly — it (a) deletes the refresh window (mainflow), and (b)
hands JSON pages to **stored procedures** that do the insert + mapping + reconcile. The procedures are
the authoritative load logic.

---

## FR-LOAD-1 — Destination wipe (mainflow; see `05` §5.3)

Before any worker loads, the mainflow deletes the refresh window:
- `DELETE FROM [Intacct].[dbo].[GLTable] WHERE Entry_Date >= {start} AND Entry_Date <= {Today_date}`
  ⚠️ Note the **delete** filters on **`Entry_Date`** while the **GL extract** filters Sage on
  **`BATCH_DATE`**. Confirm these align (OQ-2) — a mismatch could delete a different set than it reloads.
- `DELETE FROM [Intacct].[dbo].[APBillTable]` — **all rows** (full reload).

> Make log text also mentions "X records deleted from GL/APBill table" — this count comes from the
> mainflow DELETE modules (135/136), confirming the delete lives in the mainflow, not the procs.

---

## FR-LOAD-2 — Bulk insert via stored procedure (worker modules 58 GL, 56 AP)

Per fetched page, call the bulk-insert proc with the page JSON:

- **GL:** `EXEC AddGLDetailBulk @json` where `@json` = the GL page JSON (NVARCHAR(MAX), `N'…'` literal).
- **AP:** `EXEC AddAPBillBulk @json` where `@json` = the AP page JSON (VARCHAR literal — no `N` prefix in Make).

The proc parses JSON (`OPENJSON`) and inserts into the target table, mapping JSON keys → columns.

**Rebuild requirements:**
- Call the **same procs** (default) — preserves column mapping/dedup. Pass `@json` as a **bound
  parameter** (not string-interpolated), removing the `replace('→'')` escaping (`03` FR-TR-2).
- One proc call per page (≤2000 records), or batch — must remain idempotent within the windowed reload.
- Connection details fully configurable (`06`); default DB `Intacct`.
- ❓ Confirm whether `AddAPBillBulk` truly needs non-Unicode vs `AddGLDetailBulk` Unicode (`N'`) — the
  Make asymmetry may be incidental. Use Unicode-safe binding in Python regardless (OQ-3).

---

## FR-LOAD-3 — Post-load reconciliation procedures (worker modules 70, 159)

After all GL + AP pages for the window are loaded (per environment):

1. `EXEC [Intacct].[dbo].[SyncAPLinksToGL]  @StartDate={start}, @EndDate={end}`
   — joins AP bills to GL rows, sets `Stampli_Link` / `Platform_Services_Link` on GL.
2. `EXEC [Intacct].[dbo].[UpdateVendorInfoInGL] @StartDate={start}, @EndDate={end}`
   — backfills vendor id/name on GL rows.

- Both take `@StartDate`/`@EndDate` as **date** params (the window).
- ⚠️ `SyncAPLinksToGL` receives the **worker's `Report_start_date`/`Report_end_date`** (`158.*`),
  i.e. the per-chunk window — confirm whether reconcile should run per-chunk (as today) or once at the
  end over the full window (OQ-4).

---

## FR-LOAD-4 — The four stored procedures are part of the spec

| Proc | Inputs | Effect | Status |
|---|---|---|---|
| `AddGLDetailBulk` | `@json` (NVARCHAR MAX) | OPENJSON → INSERT `GLTable` | **body not yet captured — OQ-1** |
| `AddAPBillBulk` | `@json` | OPENJSON → INSERT `APBillTable` | **body not yet captured — OQ-1** |
| `SyncAPLinksToGL` | `@StartDate,@EndDate` | UPDATE `GLTable` links from AP | **body not yet captured — OQ-1** |
| `UpdateVendorInfoInGL` | `@StartDate,@EndDate` | UPDATE `GLTable` vendor cols | **body not yet captured — OQ-1** |

**Action:** extract these proc definitions from SQL Server
(`SELECT OBJECT_DEFINITION(OBJECT_ID('dbo.AddGLDetailBulk'))`, etc.) and store under
`reference/sql/`. The exact GL/AP **column lists**, types, and de-dup/merge behaviour are *defined by
these procs* and are required for true losslessness and for the parity tests in `09`.

---

## FR-LOAD-5 — Transaction & crash-safety (improvement over Make)

Make does **delete-then-insert with no enclosing transaction** → a mid-load failure leaves a table
partially populated (a real risk the optimization notes flagged).

**Rebuild requirement (target design):**
- Wrap the **window refresh** (delete + all inserts + reconcile) per environment in a strategy that is
  crash-safe. Options (decide in OQ-5):
  - **(a) Single transaction:** `BEGIN TRAN` → delete window → load pages → reconcile → `COMMIT`
    (rollback on any failure). Simplest; long-lived tran on a large GL window may be heavy.
  - **(b) Staging + atomic swap:** load into `GLTable_staging` / partition, then swap. More work,
    least lock contention, fully crash-safe.
- Either way: on failure, the live tables must remain in their pre-run state. This is the headline
  reliability win of the migration — call it out in the deliverable.

---

## FR-LOAD-6 — Connection & security
- SQL Server connection params (server, database, auth mode, user/password or AAD token) come from
  config / secret store — see `06` and the POC blocker in `../../Memory_Opti.txt` §8 (the Make
  connection's creds are redacted/unrecoverable; correct SQL credentials must be supplied).
- Default database `Intacct`; tables and proc names configurable (schema `dbo`).

---

## FR-LOAD-7 — Multi-entity keying with `Source_Env` (OQ-31; added 2026-07-06)

`GLTable`/`APBillTable` are **shared across every Sage company** (env), with **no column that
identifies the source company**. This causes two defects that this requirement fixes (proven by the
2026-07-06 multi-entity test — design: `docs/design/multi_entity_source_env_design.md`):

- **AP `Record_No` collides across companies.** `RECORDNO` is Sage's **per-company** primary key
  (sequences restart per company), so two different bills in different companies share a key. The
  `AddAPBillBulk` MERGE keys on `Record_No` **alone** → the second company silently **overwrites**
  the first (2,341 rows overwritten in the Apr–Jul test; resolution is load-order/non-deterministic).
  GL is immune (key `Record_No + Line_No` is globally unique).
- **Every delete path is entity-blind** — `wipe_window` (GL window-DELETE + AP delete) and the delta
  sweeps operate table-wide, so one env's run would delete another env's rows.

**Model (approved 2026-07-06):**
1. Add column **`Source_Env`** (`NVARCHAR(64) NOT NULL`, = the config env `name`) to both tables,
   stamped at load.
2. **AP primary key = `(Source_Env, Record_No)`** — both companies coexist; deterministic; no
   overwrite. GL key stays `(Record_No, Line_No)`; `Source_Env` added as a column for scoping.
3. **Procs carry a `@Source_Env` parameter** (a page is single-env): `AddAPBillBulk` MERGEs on
   `(Source_Env, Record_No)` and writes `Source_Env`; `AddGLDetailBulk` writes `Source_Env`.
4. **`SyncAPLinksToGL` / `UpdateVendorInfoInGL` joins add `AND G.Source_Env = A.Source_Env`** —
   `Record_ID` (the join key) is **not** unique, so without this a GL row could be stamped from the
   wrong company's bill once two companies' AP rows share a table.
5. **All deletes are `Source_Env`-scoped** — `wipe_window` and both sweeps carry `AND Source_Env = ?`
   (see `13` §13.5).

**Accepted divergence from Make:** with the AP key including `Source_Env`, both companies' bills
persist, so `APBillTable` holds **more** rows than Make's collision-collapsed prod table
(Apr–Jul test: ~27,784 vs prod's 25,443). This is a deliberate **correctness fix** — prod's count is
a lossy, non-deterministic artifact. Downstream consumers of these tables must handle the new column
and the higher AP count.

**Discriminator choice (why `Source_Env`, not `Location_ID` or `Record_ID`):** `Location_ID`
(= `MEGAENTITYID`) is not a clean company key — it is NULL on many rows and one company spans many
locations. `Record_ID` (document number) is not unique (e.g. `"Placker Charles REFUND"` on multiple
bills). `RECORDNO` was verified unique **within** each company (Evergreen 20,834 / Veritashs 2,204,
0 in-company dups) — so `(Source_Env, Record_No)` is a valid PK.

**Isolation:** all of the above is applied to the **`test_`** tables and **`test_`** procs only; the
prod tables/procs Make uses are untouched. The equivalent prod schema/proc change is a separate,
gated cutover step coordinated with the DBA + downstream owners (OQ-31 a–d). This is the one place
CLAUDE.md's "procs are black boxes" rule is deliberately opened — the merge key lives in the proc.
