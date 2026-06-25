# 05 — Orchestration & Scheduling (Mainflow)

**Source of truth:** Make scenarios `Intaact feeds to SQL mainflow_8:00AM V 1.0` (id `1743529`)
and `Intaact feeds to SQL mainflow_5:30PM V 1.0` (id `4066399`).
Sanitized reference: `reference/mainflow_8am_1743529.sanitized.json`,
`reference/mainflow_530pm_4066399.sanitized.json`.

> **Verified fact:** the two mainflow scenarios are **structurally identical** — same module
> ids `{2,96,99,135,136,160,161,…,182}`, same repeat counts (26 / 5), same worker URLs, same
> 30s sleeps. The **only** difference is the schedule time. ⇒ There is exactly **one** mainflow
> algorithm; it runs **twice per day**.

---

## 5.1 Purpose

The mainflow is the **scheduler / date-slicer / table-refresher**. It does **not** talk to Sage.
Its job each run is:

1. Decide the **refresh window** and **run type** from today's date.
2. **Wipe** the destination SQL tables for that window (full-refresh strategy).
3. **Slice** the window into **calendar-month chunks**.
4. **Fan out** each month chunk to a worker scenario (the actual Extract→Transform→Load),
   throttled by a **30-second sleep** between chunks.

> In the Python rebuild, items 1–4 collapse into a single orchestrator function; the per-chunk
> HTTP fan-out and the 30s sleeps **disappear** (replaced by direct in-process calls / one windowed
> query). See `00_overview.md`.

---

## 5.2 Run-type & window derivation  (module `2`, `util:SetVariables`, scope=roundtrip, runs once)

Three roundtrip variables are computed from `now` at start of run:

| Variable | Make IML formula (verbatim) | Meaning | Python equivalent |
|---|---|---|---|
| `Today_date` | `formatDate(addDays(addMonths(setDate(now; 1); 1); -1); "MM/DD/YYYY")` | **Last calendar day of the current month** | `(first_of_month + 1 month) - 1 day` |
| `Report_start_date` | `if(formatDate(now;"DD") != 1; formatDate(addMonths(now;-3);"MM/01/YYYY"); formatDate(addYears(now;-1);"01/01/YYYY"))` | If today is **not** the 1st → **1st day of the month 3 months ago**. If today **is** the 1st → **Jan 1 of last year**. | conditional on `now.day == 1` |
| `run_type` | `if(formatDate(now;"DD") != 1; 1; 2)` | `1` = **daily run** (any day except the 1st). `2` = **monthly run** (the 1st of the month). | `2 if now.day==1 else 1` |

**Net effect**
- **Daily run** (run_type=1, most days): refresh window = **start of 3-months-ago → end of current month** (≈ 4 calendar months).
- **Monthly run** (run_type=2, on the 1st): refresh window = **Jan 1 of last year → end of current month** (≈ 16–28 months; full historical reload).

`Today_date` (current month-end) is the **upper bound** used both for the SQL delete and to clamp the final month chunk.

**Error handling:** module `2` has an `onerror` `builtin:Break` (retry **3×**, interval **15s**).

---

## 5.3 Destination table wipe (full-refresh)

| Module | Type | Target table | Filter | Behaviour |
|---|---|---|---|---|
| `135` | `mssql:DeleteFromTable` | `[Intacct].[dbo].[GLTable]` | `Entry_Date >= {{2.Report_start_date}}` **AND** `Entry_Date <= {{2.Today_date}}` | Delete **only the refresh window** of GL rows |
| `136` | `mssql:DeleteFromTable` | `[Intacct].[dbo].[APBillTable]` | *(empty `where`)* | Delete **ALL** rows (full wipe) |

- Connection: mssql account id `2803828` ("My Microsoft SQL Server connection").
- `isToManageDataInSharedTransaction: true`, `isolationLevel: ""` (default).
- Each has an `onerror` `builtin:Break` (retry 3×, 15s).
- `135.rowsAffected` / `136.rowsAffected` are captured for the log line.

> ⚠️ **Design risk to fix in rebuild (carried from optimization notes):** this is a naive
> *delete-then-reload* with **no transaction wrapping the delete + the subsequent inserts**.
> If a worker fails mid-load, the table is left **partially populated**. The Python target should
> use **load-to-staging + atomic swap** (or a single transaction) so the table is never half-empty.
> This is an explicit improvement over the Make behaviour, not a replication of a bug — flag in
> `09_acceptance_criteria.md`.

> ❓ **GLTable delete is windowed but APBillTable delete is full** — confirm this asymmetry is
> intentional (see `10_open_questions.md`). Likely: GL is large/incremental; APBill is small enough
> to fully reload each run.

---

## 5.4 Run log (Make `datastore` "Log_text_SQLFeeds", id `23497`, key `"1"`)

| Module | Type | Action |
|---|---|---|
| `96` | `datastore:AddRecord` (overwrite=true) | Reset log record key `1` → `Description = "Start of process\n"` |
| `162` | `datastore:GetRecord` key `1` | Read current log text |
| `99` | `datastore:UpdateRecord` key `1` | Append: `"{{135.rowsAffected}} records deleted from GL table\n{{136.rowsAffected}} records deleted from APBill table\n"` |

⇒ In Python this is just a **run-log string / log file / log table row**. The Make datastore is an
implementation detail, not a requirement.

---

## 5.5 Branch + month-slicing loop (router `160`)

Router `160` has two routes, gated by `run_type`:

### Route A — Monthly (`run_type == 2`)
- `163` `builtin:BasicRepeater`: `start=1, step=1, repeats=26`, filter `{{2.run_type}} == "2"`. Loop index = `163.i`.
- Per iteration:
  - `165` `util:GetVariables` → reads previous `Report_start_date`, `Report_end_date`.
  - `164` `util:SetVariables` (roundtrip) computes the next month window:
    - `Report_start_date = if(163.i = 1; formatDate(2.Report_start_date;"MM/DD/YYYY"); formatDate(addDays(165.Report_end_date; 1);"MM/DD/YYYY"))`
    - `Report_end_date   = if(163.i = 1; formatDate(addDays(addMonths(2.Report_start_date; 1); -1);"MM/DD/YYYY"); formatDate(addMonths(165.Report_end_date; 1);"MM/DD/YYYY"))`
    - i.e. **iteration 1** → window = `[global start , end-of-that-month]`; **iteration n** → `start = prev_end + 1 day`, `end = prev_end + 1 month`.
  - `171` `builtin:BasicRouter`, gate filter `{{164.Report_start_date}} <= {{2.Today_date}}` (only process windows that start on/before the current month-end). Two sub-routes:
    - **Sub-route 1** `169` `util:SetVariables`, filter `{{164.Report_end_date}} >= {{2.Today_date}}` → **clamp** `Report_end_date = {{2.Today_date}}` (last partial window stops at current month-end).
    - **Sub-route 2** `172` GetVars → `173` `http:ActionSendData` **POST** → `174` `util:FunctionSleep 30s`.
      - URL: `https://hook.us2.make.com/xtxrfsh8obcv8kbxnvrqqfmzpm3o4o4b` → **worker `monthly run` (id 1744512, hook 748921)**.
      - Body: `{ "Report_start_date":"{{172.Report_start_date}}", "Report_end_date":"{{172.Report_end_date}}", "Instance":"{{163.i}}" }`
      - `parseResponse:true`, `handleErrors:true`, gzip on.

### Route B — Daily (`run_type == 1`)
- `176` `builtin:BasicRepeater`: `start=1, step=1, repeats=5`, filter `{{2.run_type}} == "1"`. Loop index = `176.i`.
- Per iteration (same shape as monthly):
  - `177` GetVars → `178` SetVars (identical formulas, using `176.i` / `177.Report_end_date`).
  - `179` BasicRouter gate `{{178.Report_start_date}} <= {{2.Today_date}}`:
    - **Sub-route 1** `180` SetVars, filter `{{178.Report_end_date}} >= {{2.Today_date}}` → clamp `Report_end_date = Today_date`.
    - **Sub-route 2** `181` GetVars → `161` `http:ActionSendData` POST → `182` sleep 30s.
      - URL: `https://hook.us2.make.com/rod4msq57dhbctv83py83rbgbxnap7tz` → **worker `daily run` (id 1744137, hook 748767)**.
      - Body: `{ "Report_start_date":"{{181.Report_start_date}}", "Report_end_date":"{{181.Report_end_date}}" }`
        **NOTE: daily payload has NO `Instance` field** (monthly does). Confirm whether the daily worker needs/ignores it.

### Slicing summary
| Run | Repeats | Window span | Chunk size | Worker | Payload |
|---|---|---|---|---|---|
| Daily (run_type 1) | 5 | ~4 months (3-months-ago → month-end) | 1 calendar month | `daily run` (rod4…) | `{start,end}` |
| Monthly (run_type 2) | 26 | Jan-1-last-year → month-end | 1 calendar month | `monthly run` (xtxrf…) | `{start,end,Instance}` |

The loop stops processing once a chunk's start exceeds `Today_date` (gate at `171`/`179`); the final
chunk's end is clamped to `Today_date`. Excess repeats (windows beyond now) are no-ops.

---

## 5.6 Scheduling

- Two scheduled scenarios fire the **same** mainflow logic:
  - `1743529` ≈ **08:00** ("8:00AM")
  - `4066399` ≈ **17:30** ("5:30PM")
- `metadata.instant = false` (time-scheduled, not webhook-triggered).
- ❓ Exact cron expression, timezone, and active weekdays are **not** in the blueprint body —
  confirm from the Make scenario scheduling settings (see `10_open_questions.md`).

**Rebuild requirement:** the Python orchestrator must be runnable on a schedule (SQL Server Agent /
Windows Task Scheduler / cron) **twice daily**, with the schedule times configurable (see
`06_configuration_parameters.md`). The `run_type` (daily vs monthly) must remain **derived from the
run date** exactly as in §5.2 (so the 1st-of-month run still does the full reload), **and** be
overridable via config/CLI for manual backfills.

---

## 5.7 Scenario metadata (both mainflows)

```
roundtrips: 1   sequential: false   maxErrors: 3   dlq: true
autoCommit: true   freshVariables: false   instant: false   autoCommitTriggerLast: true
```

---

## 5.8 What the Python rebuild keeps vs drops

| Make element | Rebuild |
|---|---|
| run_type / window derivation (§5.2) | **KEEP** (port the date logic exactly; make overridable) |
| GLTable windowed delete + APBill full delete (§5.3) | **KEEP the intent**, upgrade to staging+atomic swap (transaction-safe) |
| Run log datastore (§5.4) | Replace with structured logging (file/table) |
| Month-by-month slicing + per-chunk HTTP fan-out (§5.5) | **DROP the fan-out**; loop chunks in-process (or one windowed query) |
| 30s `FunctionSleep` between chunks (§5.5) | **DROP** (only existed to dodge webhook/API rate limits) |
| Two separate scheduled scenarios | One orchestrator entrypoint scheduled twice daily |
