# 00 — Overview & Architecture

## Purpose

Replace the Make.com **"Intaact feeds to SQL"** scenario family with a single configurable Python
ETL that pulls **General Ledger detail** and **AP Bill** data from **Sage Intacct** and loads it into
a **SQL Server** database (`Intacct.dbo.GLTable`, `Intacct.dbo.APBillTable`), applying the same
windowing, filtering, and post-load reconciliation the Make scenarios do today.

This is **functional parity** work: the Python output must match what Make produces today (same rows,
same values), while removing ~130K Make operations/cycle. See `../README.md` and `../../Memory_Opti.txt`.

## Source system being replaced (Make, team 33002)

```
                       ┌─────────────────────────────────────────────┐
   SCHEDULE 08:00 ───► │ Mainflow (1743529)  ── identical logic ──┐   │
   SCHEDULE 17:30 ───► │ Mainflow (4066399)                       │   │
                       └──────────────────────────────────────────┼───┘
                                                                   │
        derive run_type from date; wipe SQL window; slice into     │
        calendar-month chunks; POST each chunk (+30s sleep) to:    │
                                                                   ▼
        run_type=2 (1st of month) ─► monthly-run worker (1744512, hook xtxrf…)
        run_type=1 (other days)   ─► daily-run  worker (1744137, hook rod4…)
                                                                   │
                          (workers are functionally identical)     ▼
              ┌──────────────────────────────────────────────────────────────┐
              │ WORKER: per environment (from Config.xlsx Env_data):          │
              │   login to Sage  →  [GL pipeline] + [AP pipeline]  →          │
              │   reconcile procs  →  log to datastore + SharePoint           │
              │   GL:  query GLDETAIL  (offset-paged 2000) → AddGLDetailBulk  │
              │   AP:  query APBILL    (offset-paged 2000) → AddAPBillBulk    │
              │   then: SyncAPLinksToGL, UpdateVendorInfoInGL (per window)    │
              └──────────────────────────────────────────────────────────────┘
```

## Target architecture (Python rebuild)

One orchestrator + one worker module, fully config-driven. The Make scaffolding (webhook fan-out,
30s sleeps, datastore message-bus, per-chunk HTTP) collapses into in-process calls.

```
intacct_sql_etl  (scheduled twice daily, or run on demand)
│
├─ orchestrator
│   1. derive run_type + refresh window from run date (config-overridable)   [spec 05]
│   2. open ONE SQL transaction / staging strategy                            [spec 04]
│   3. wipe destination window (GL windowed, APBill full)                     [spec 04/05]
│   4. for each environment in config:                                        [spec 06]
│        worker.run(env, start_date, end_date, instance)
│   5. commit / atomic swap; write run log                                    [spec 04/07]
│
└─ worker(env, start, end, instance)                                          [spec 02/03/04]
    a. login to Sage Intacct (session id)                                     [spec 02]
    b. extract GL detail  (probe count → offset-page loop)                    [spec 02]
    c. extract AP bill    (probe count → offset-page loop)                    [spec 02]
    d. load each page → GLTable / APBillTable (bulk, JSON→proc or native)     [spec 04]
    e. reconcile: SyncAPLinksToGL, UpdateVendorInfoInGL                       [spec 04]
    f. log progress                                                           [spec 07]
```

## Source → Target data summary

| Source (Sage Intacct object) | Filter | Destination table | Load proc (today) |
|---|---|---|---|
| `GLDETAIL` (17 fields) | `BATCH_DATE` in window **AND** `BOOKID='ACCRUAL'` | `Intacct.dbo.GLTable` | `AddGLDetailBulk(@json)` |
| `APBILL` (7 fields, `showprivate`) | `WHENPOSTED` in window | `Intacct.dbo.APBillTable` | `AddAPBillBulk(@json)` |
| (post-load join AP→GL) | window | `Intacct.dbo.GLTable` (update) | `SyncAPLinksToGL`, `UpdateVendorInfoInGL` |

## Key design decisions (carried into the spec)

1. **Server-side mapping stays server-side.** Make does *no* client field mapping — it hands the raw
   Sage JSON to SQL stored procedures (`AddGLDetailBulk`, `AddAPBillBulk`) that map JSON→columns via
   `OPENJSON`. The Python rebuild should **reuse these existing stored procedures** (they encode the
   real column mapping + dedup/insert logic we must not lose). Reproducing them in Python is out of
   scope unless we deliberately choose to; default = call the procs. See `04_sql_load.md` + open
   question OQ-1.
2. **Transaction safety upgrade.** Make does naive delete-then-load with no transaction. The Python
   version should wrap the window refresh in a transaction or use staging + atomic swap so the tables
   are never left half-empty. (Improvement, not parity — flagged in `09_acceptance_criteria.md`.)
3. **Everything configurable.** Connections, environments, window sizing, page size, filters
   (`BOOKID`, date fields), schedule, retry policy — all via config, no hardcoding. See
   `06_configuration_parameters.md`.
4. **Multi-environment.** The worker runs per environment listed in config (Make reads them from
   `Config.xlsx`/`Env_data`), each with its own Sage credentials.

## Document map

| Spec | Covers | Source |
|---|---|---|
| `01_functional_requirements.md` | Numbered FRs / NFRs | both |
| `02_sage_extraction.md` | Sage login, GL & AP queries, pagination | workers |
| `03_transform_business_rules.md` | Variables, JSON encoding, business rules, dates | workers + mainflow |
| `04_sql_load.md` | Bulk load, stored procs, transaction strategy | workers |
| `05_orchestration_and_scheduling.md` | run_type, windowing, scheduling, table wipe | mainflows |
| `06_configuration_parameters.md` | Every configurable attribute | all |
| `07_logging_error_handling.md` | Retries, logging, idempotency | all |
| `08_make_reference_map.md` | Module-id → requirement traceability | all |
| `09_acceptance_criteria.md` | Parity tests vs Make | — |
| `10_open_questions.md` | To confirm with user / Sage / DBA | — |
