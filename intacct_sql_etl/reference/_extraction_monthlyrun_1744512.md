# Raw lossless extraction — Worker "Intaact feeds to SQL monthly run" (scenario 1744512)

> Reverse-engineered module-by-module from `scratch/sql_monthlyrun_1744512.json` (read in full).

## VERDICT: functionally IDENTICAL to the daily-run worker (1744137)

The monthly worker is the **same ETL** as `_extraction_dailyrun_1744137.md` — same module ids,
same 3-route structure, **byte-identical** Sage XML queries (GL `GLDETAIL` + AP `APBILL`), same
offset pagination, same SQL load (`AddGLDetailBulk` / `AddAPBillBulk`), same reconcile procs
(`SyncAPLinksToGL` / `UpdateVendorInfoInGL`), same per-environment outer loop, same retry policy.

Refer to `_extraction_dailyrun_1744137.md` for the full module detail. **Only the differences are
listed below.**

## DIFFERENCES vs daily worker

| Aspect | Daily (1744137) | Monthly (1744512) |
|---|---|---|
| Webhook hook id | 748767 (`Daily_run`) | 748921 (`My gateway-webhook webhook`) |
| Trigger input fields | `Report_start_date`, `Report_end_date` | `Report_start_date`, `Report_end_date`, **`Instance`** |
| Module 2 vars | Today_date, Report_start_date | Today_date, Report_start_date, **`instance_no = {{158.Instance}}`** |
| Datastore log lines | no Instance prefix | prefixed **`Instance{{158.Instance}}:`** (modules 103/115/116/124/127/134/137) |
| SharePoint log filename (119) | `Dailyrun_log_{{formatDate(now;"MMDDYYYY")}}.txt` | `Monthlyrun_log_{{formatDate(now;"MMDDYYYY")}}.txt` |
| Final log line (137) | `…Processing complete` | `…Instance{{158.Instance}} processing complete` |

Everything else — Sage objects/fields/filters/orderby/pagesize, the `numremaining+1` (GL) vs
`totalcount` (AP) count logic, `replace('→'')` escaping, the two `mssql:Query` bulk loads, the two
stored procedures with `@StartDate/@EndDate`, Excel `Env_data` read, per-env `Prod_Sage_<env>` creds,
all `builtin:Break` retry=3/interval=15 handlers, scenario metadata (roundtrips 1, dlq true,
maxErrors 3) — is **identical**.

## Implication for the Python rebuild
There is **one** worker ETL implementation. "daily" vs "monthly" is purely the **caller** (mainflow
window span + `Instance` tag for logging). The Python worker takes `(start_date, end_date,
instance=None)` and runs the same code path; `instance` only affects log labelling.
