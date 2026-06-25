# 08 — Make → Requirement Traceability Map

Maps every Make module to its requirement, so nothing is dropped. Sanitized blueprints in
`reference/*.sanitized.json`; full extractions in `reference/_extraction_*.md`.

## Scenario inventory
| Role | Name | id | Hook | Reference |
|---|---|---|---|---|
| Mainflow 08:00 | Intaact feeds to SQL mainflow_8:00AM V 1.0 | 1743529 | scheduled | mainflow_8am_*.sanitized.json |
| Mainflow 17:30 | Intaact feeds to SQL mainflow_5:30PM V 1.0 | 4066399 | scheduled | mainflow_530pm_*.sanitized.json |
| Worker daily | Intaact feeds to SQL_daily run | 1744137 | rod4…/748767 | _extraction_dailyrun_1744137.md |
| Worker monthly | Intaact feeds to SQL monthly run | 1744512 | xtxrf…/748921 | _extraction_monthlyrun_1744512.md |

> Mainflows 1743529 & 4066399 are structurally identical (only schedule differs). Workers 1744137 &
> 1744512 are functionally identical (only `Instance`/log labels/hook differ).

## Mainflow modules (1743529 / 4066399)
| Module | Type | Requirement |
|---|---|---|
| 96 | datastore:AddRecord | FR-17 (log "Start of process") |
| 2 | util:SetVariables | FR-1, FR-2 (run_type, Today_date, Report_start_date) — `05 §5.2` |
| 162 | datastore:GetRecord | FR-17 |
| 135 | mssql:DeleteFromTable GLTable | FR-13 (windowed delete on `Entry_Date`) |
| 136 | mssql:DeleteFromTable APBillTable | FR-13 (full wipe) |
| 99 | datastore:UpdateRecord | FR-17 (deleted counts) |
| 160 | BasicRouter (run_type) | FR-1 |
| 163 / 176 | BasicRepeater (26 / 5) | FR-3 (monthly/daily chunking) |
| 165/164/172, 177/178/181 | Get/SetVariables | FR-2, FR-3 (month-window math) |
| 171/169, 179/180 | Router + clamp | FR-3 (gate start≤today; clamp end=today) |
| 173 / 161 | http POST worker | FR-3, FR-5 (fan-out → worker; **dropped** in rebuild) |
| 174 / 182 | FunctionSleep 30 | rate-limit plumbing (**dropped**) |

## Worker modules (1744137 / 1744512)
| Module | Type | Requirement |
|---|---|---|
| 158 | gateway:CustomWebHook | FR-3 input `{start,end,Instance?}` (rebuild: function args) |
| 2 | util:SetVariables | FR-TR-1 (Today_date=end, start, instance_no) |
| 1 | excel:listTableRows Env_data | FR-5 (environments → config) |
| 3 | datastore:GetRecord Sage_creds | FR-5/6 (per-env creds `Prod_Sage_<env>`) |
| 90 / 11 | Set/GetVariables Sage_* | FR-6, FR-TR-1 |
| 12 | http getAPISession | FR-6 (login/session) |
| 13 | BasicRouter (3 routes) | FR-EXT-6 (GL ∥ AP ∥ reconcile) |
| 15 | http GL probe | FR-9 (GL count = numremaining+1) |
| 17 | SetVariables | FR-9, FR-TR-1 |
| 64 | BasicRepeater | FR-9 (GL page loop) |
| 20 | GetVariables offset | FR-9 |
| 18 | http GL page | FR-7 (GLDETAIL query) |
| 140 | Router length>0 | FR-10 (skip empty/failed) |
| 81 | json:TransformToJSON | FR-11 |
| 21 | SetVariables offset+=2000, sql_data | FR-9, FR-TR-2 |
| 58 | mssql:Query AddGLDetailBulk | FR-14 |
| 114 | BasicAggregator (feeder 64) | loop terminator |
| 122 / 123/124 | Router + log | FR-10, FR-EXT-5 (GL error) |
| 27 | http AP probe | FR-9 (AP count = totalcount) |
| 28 | SetVariables | FR-9 |
| 29 | BasicRepeater | FR-9 (AP page loop) |
| 30 | GetVariables offset | FR-9 |
| 31 | http AP page | FR-8 (APBILL query, showprivate) |
| 141 | Router length>0 | FR-10 |
| 129 | json:TransformToJSON | FR-11 |
| 32 | SetVariables | FR-9, FR-TR-2 |
| 56 | mssql:Query AddAPBillBulk | FR-14 |
| 113 | BasicAggregator (feeder 29) | loop terminator |
| 125 / 126/127 | Router + log | FR-10, FR-EXT-5 (AP error) |
| 71 | FunctionSleep 3 | FR-TR-6 (settle; configurable) |
| 70 | mssql:StoredProcedure SyncAPLinksToGL | FR-15 |
| 159 | mssql:StoredProcedure UpdateVendorInfoInGL | FR-15 (no retry — see `07`) |
| 102/103/111/112/115/116/120/123/124/126/127/133/134/137 | datastore log | FR-17 |
| 110 | BasicAggregator (feeder 1) | FR-5 (per-env loop terminator) |
| 119 | sharepoint:uploadAFile | FR-17 (log persist; optional OQ-6) |
| onerror Breaks (143–155) | builtin:Break 3×/15min | NFR-5 (retry) |

## External resources to recreate
| Resource | Make ref | Rebuild |
|---|---|---|
| Sage creds per env | datastore 1862 `Sage_creds` key `Prod_Sage_<env>` | secret store (`06`) |
| Environment list | Excel `Config.xlsx`/`Env_data` (conn 450334) | `environments[]` config (`06`) |
| SQL Server | conn 2803828, DB `Intacct` | `sql.*` config (`06`) |
| Stored procs | AddGLDetailBulk, AddAPBillBulk, SyncAPLinksToGL, UpdateVendorInfoInGL | capture to `reference/sql/` (OQ-1) |
| Run-log store | datastore 23497 + SharePoint Logs folder | `log.*` config (`07`) |
