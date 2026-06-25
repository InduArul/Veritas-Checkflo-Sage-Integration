# Intacct → SQL Server ETL (Python rebuild of the Make "Intaact feeds to SQL" family)

Replace the Make.com **"Intaact feeds to SQL"** scenario family with a single, configurable
Python ETL: **Extract** GL + AP Bill data from Sage Intacct → **Transform** / apply business rules
→ **Load** into SQL Server (`Intacct.dbo.GLTable`, `Intacct.dbo.APBillTable`).

Motivation: that family consumes ~130K Make operations/cycle (≈18% of total) doing an ETL-type
data copy — the wrong workload for an ops-metered iPaaS. Moving it to Python on the existing SQL
Server is **cost-negative** (removes the ops, adds ~$0). See `../Memory_Opti.txt`.

## Methodology: Spec-Driven Development

We build the **requirement specification first**, reverse-engineered losslessly from the live Make
blueprints (the authoritative behaviour), then implement against it. **No code before the spec is
reviewed and signed off.**

## Folder structure

```
intacct_sql_etl/
├── README.md                ← this file
├── specs/                   ← THE deliverable for step 1 (requirement specification)
│   ├── 00_overview.md            architecture, scope, source→target summary
│   ├── 01_functional_requirements.md
│   ├── 02_sage_extraction.md     worker: Sage Intacct API extraction (objects, queries, paging)
│   ├── 03_transform_business_rules.md  worker: mappings, derived fields, business rules
│   ├── 04_sql_load.md            worker: SQL Server load (tables, columns, procs, transactions)
│   ├── 05_orchestration_and_scheduling.md   mainflow: run_type, windowing, scheduling  ✅ drafted
│   ├── 06_configuration_parameters.md   every configurable attribute → config keys
│   ├── 07_logging_error_handling.md  retries, DLQ behaviour, logging, idempotency
│   ├── 08_make_reference_map.md   module-by-module map back to the blueprints (traceability)
│   ├── 09_acceptance_criteria.md  parity tests vs Make (row counts, checksums)
│   └── 10_open_questions.md       items to confirm with user / Sage / DBA
├── config/
│   └── config.example.yaml  ← template; all configurable attributes (no secrets)
├── reference/               ← sanitized source blueprints (secrets/PII stripped) for traceability
│   ├── sanitize_blueprints.py
│   ├── mainflow_8am_1743529.sanitized.json
│   ├── mainflow_530pm_4066399.sanitized.json
│   ├── worker_dailyrun_1744137.sanitized.json
│   └── worker_monthlyrun_1744512.sanitized.json
├── src/                     ← implementation (AFTER spec sign-off)
└── tests/                   ← parity + unit tests (AFTER spec sign-off)
```

## Source Make scenarios (live, team 33002)

| Role | Scenario | id | Hook |
|---|---|---|---|
| Mainflow (08:00) | Intaact feeds to SQL mainflow_8:00AM V 1.0 | 1743529 | scheduled |
| Mainflow (17:30) | Intaact feeds to SQL mainflow_5:30PM V 1.0 | 4066399 | scheduled |
| Worker (daily)   | Intaact feeds to SQL_daily run | 1744137 | rod4…/748767 |
| Worker (monthly) | Intaact feeds to SQL monthly run | 1744512 | xtxrf…/748921 |
| Backup (off)     | Backup_Intaact feeds to SQL | 1359324 | inactive |

## Status

- [x] Step 0 — Architecture mapped; all 4 blueprints pulled + sanitized into `reference/`; lossless
      module-by-module extractions written (`reference/_extraction_*.md`).
- [x] **Step 1 — Requirement specification COMPLETE** (`specs/00`–`10` + `config.example.yaml`).
      Awaiting user review/sign-off.
- [ ] Step 1b — Capture the 4 stored-proc bodies + table DDL into `reference/sql/` (OQ-1; needs DB access).
- [ ] Step 1c — Resolve open questions in `specs/10_open_questions.md` with user/DBA.
- [ ] Step 2 — Implementation (`src/`) — only after sign-off.
- [ ] Step 3 — Parity validation vs Make (parallel run), then cutover.
