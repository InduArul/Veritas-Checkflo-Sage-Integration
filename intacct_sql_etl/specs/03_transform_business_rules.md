# 03 — Transform & Business Rules (Worker + Mainflow)

The Make implementation does **almost no client-side transformation** — it passes Sage's JSON straight
to SQL stored procedures that map and reconcile server-side. The "transform" layer in Make is limited
to (a) variable wiring, (b) JSON serialization + SQL-escaping, (c) the date-window math (in the
mainflow, see `05`). All genuine business rules live in either the **Sage query filters** (this doc)
or the **SQL stored procedures** (`04`).

---

## FR-TR-1 — Worker variable wiring (no business logic, but must be reproduced)

| Make module | Var(s) | Source | Notes |
|---|---|---|---|
| 2 | `Report_start_date` | webhook `Report_start_date` | window start |
| 2 | `Today_date` | webhook **`Report_end_date`** | ⚠️ `Today_date` here = the window END, not "today". Used as the upper bound in Sage filters. |
| 2 | `instance_no` | webhook `Instance` (monthly only) | log label only |
| 90 | `Sage_URL, Sage_sender_id, Sage_Sender_pass, Sage_Company_ID, Sage_user_id, Sage_User_Password` | creds store `Prod_Sage_<env>` | per-environment Sage credentials |
| 17 | `Total_records_count` (GL), `offset=0`, `sage_session_id` | probe 15 + login 12 | GL: `numremaining + 1` |
| 28 | `Total_records_count` (AP), `offset=0`, `sage_session_id` | probe 27 + login 12 | AP: `totalcount` |
| 21 / 32 | `offset += 2000`, `sql_data` | page loop | see FR-TR-2 |

---

## FR-TR-2 — JSON serialization + SQL escaping (modules 81/129 + 21/32)

For each fetched page:
1. Serialize the Sage page array to a **compact JSON string** (no indentation) — Make `TransformToJSON`
   on `18.data` (GL) / `31.data` (AP).
2. **Escape single quotes**: `sql_data = replace(json, "'", "''")` — required because Make injects the
   string into a `DECLARE @json NVARCHAR(MAX) = '<...>'` statement.

> **Rebuild note:** prefer a **parameterized query** (`pyodbc` `?` / `params`) passing the JSON as a
> bind variable — this eliminates the quote-escaping hack and SQL-injection surface entirely while
> calling the same `AddGLDetailBulk` / `AddAPBillBulk` procs. The `replace('→'')` step then becomes
> unnecessary. (Behavioural parity preserved; mechanism improved — note in `09`.)

The JSON keys passed = the Sage SELECT field names (see `02`). No renaming, no type coercion, no field
dropping happens in Make — the stored proc consumes the keys directly.

---

## FR-TR-3 — Business rules embedded in the Sage queries

These are the **only** explicit business filters in the Make layer (everything else is in SQL procs):

| Rule | Where | Detail | Configurable? |
|---|---|---|---|
| **GL book filter** | GL query | `BOOKID = 'ACCRUAL'` — only accrual-book GL detail is fed to SQL | **Yes** (default `ACCRUAL`) |
| **GL date basis** | GL query | window applied on `BATCH_DATE` (not ENTRY_DATE) | Yes (field configurable) |
| **AP date basis** | AP query | window applied on `WHENPOSTED` | Yes (field configurable) |
| **AP private records** | AP query | `showprivate=true` (include private/draft bills) | Yes |
| **GL ordering** | GL query | `AUWHENCREATED, RECORDNO, LINE_NO` — defines insert order (matters if proc relies on order) | Yes |
| **AP ordering** | AP query | `RECORDNO` | Yes |

> ⚠️ `BOOKID='ACCRUAL'` and the date-basis fields (`BATCH_DATE`, `WHENPOSTED`) are **real business
> semantics** — they decide which financial rows land in the warehouse. They must be preserved exactly
> and surfaced as config, not buried in code.

---

## FR-TR-4 — Business rules embedded in the SQL stored procedures (see `04`)

The heavy transform — JSON→column mapping, type conversion, de-duplication, and the AP→GL link/vendor
joins — lives in four SQL Server stored procedures owned by the database, **not** in Make:

| Proc | Role |
|---|---|
| `AddGLDetailBulk(@json)` | parse GL JSON → insert into `GLTable` (column mapping + types) |
| `AddAPBillBulk(@json)` | parse AP JSON → insert into `APBillTable` |
| `SyncAPLinksToGL(@StartDate,@EndDate)` | join AP bills → set `Stampli_Link` / `Platform_Services_Link` on GL rows in window |
| `UpdateVendorInfoInGL(@StartDate,@EndDate)` | backfill vendor id/name on GL rows in window |

**Decision needed (OQ-1):** the Python rebuild should, by default, **call these existing procs** so
the column mapping/reconciliation logic is preserved verbatim. We must obtain and version their source
(`reference/sql/`) so the spec is truly complete and so we can (optionally, later) port them. Until we
read the proc bodies, the exact GL/AP column mappings and dedup behaviour are **defined by the procs**,
not by this document.

---

## FR-TR-5 — Date semantics (shared with mainflow `05`)

- All dates are **`MM/DD/YYYY`** strings end-to-end (Sage filter values, SP `@StartDate/@EndDate`).
- The window `[Report_start_date, Report_end_date]` is produced by the **mainflow** (see `05` §5.2/5.5)
  and passed to the worker; the worker treats `Report_end_date` as the inclusive upper bound
  (`Today_date`).
- run_type (daily/monthly) and window span are **mainflow** concerns (`05`); the worker is window-agnostic.

---

## FR-TR-6 — Post-load reconciliation ordering

Within a window/environment, the order is **fixed and must be preserved**:
1. Load **all** GL pages → `GLTable`.
2. Load **all** AP pages → `APBillTable`.
3. Wait ~3s (Make module 71 — settle; can be dropped or made configurable in rebuild).
4. `SyncAPLinksToGL(start, end)`.
5. `UpdateVendorInfoInGL(start, end)`.

Steps 4–5 depend on both 1 and 2 being complete (they join AP data onto GL rows).
