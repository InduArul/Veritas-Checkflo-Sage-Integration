# 06 — Configuration Parameters

Every attribute that is hardcoded or connection-bound in Make becomes config here. Secrets live in a
separate secret store / `.env` (git-ignored), referenced by key — never in the main config or VCS.
The canonical template is `config/config.example.yaml`.

## 6.1 Parameter inventory (Make source → config key)

### Global / run
| Config key | Default | Make source | Notes |
|---|---|---|---|
| `run.schedule_times` | `["08:00","17:30"]` | mainflow schedules (1743529/4066399) | when the orchestrator runs |
| `run.timezone` | _confirm_ | Make org TZ | OQ-8 |
| `run.run_type_override` | `null` (auto) | mainflow `run_type` logic | `auto`/`daily`/`monthly` for backfills |
| `run.window_override` | `null` | — | explicit `{start,end}` for manual backfill |
| `run.daily_lookback_months` | `3` | mainflow `addMonths(now;-3)` | start of daily window |
| `run.monthly_start` | `Jan 1 last year` rule | mainflow `addYears(now;-1)` | start of monthly window |
| `run.chunk` | `calendar_month` | mainflow repeaters/window math | slicing granularity |
| `run.date_format` | `%m/%d/%Y` | Make `MM/DD/YYYY` | Sage + proc date format |

### Environments (replaces `Config.xlsx` / `Env_data`)
| Config key | Default | Make source | Notes |
|---|---|---|---|
| `environments[]` | _required_ | Excel `Env_data` (module 1) | list of env names to process |
| `environments[].name` | — | `1.values.0` | e.g. `Evergreen` |
| `environments[].sage_creds_key` | `Prod_Sage_<name>` | datastore `Sage_creds` (1862) | secret-store lookup key |
| `environments[].enabled` | `true` | — | toggle per env |

### Sage Intacct (per environment; secrets in secret store)
| Config key | Default | Make source | Notes |
|---|---|---|---|
| `sage.url` | `https://api.intacct.com/ia/xml/xmlgw.phtml` | creds `URL` | endpoint |
| `sage.sender_id` / `sage.sender_password` | secret | creds | control block |
| `sage.user_id` / `sage.user_password` | secret | creds `login` | auth |
| `sage.company_id` | secret/config | creds | company |
| `sage.dtd_version` | `3.0` | XML control | |
| `sage.page_size` | `2000` | query `pagesize` | pagination |
| `sage.request_timeout_s` | _default_ | http timeout | |
| `sage.gzip` | `true` | http gzip | |

### Sage query / business rules
| Config key | Default | Make source | Notes |
|---|---|---|---|
| `gl.object` | `GLDETAIL` | GL query | |
| `gl.date_field` | `BATCH_DATE` | GL filter | business: date basis |
| `gl.book_filter` | `ACCRUAL` | GL filter `BOOKID` | **business rule** |
| `gl.select_fields` | (17, see `02`) | GL select | order significant |
| `gl.order_by` | `AUWHENCREATED, RECORDNO, LINE_NO` | GL orderby | |
| `gl.count_basis` | `numremaining+1` | module 17 | pagination count source |
| `ap.object` | `APBILL` | AP query | |
| `ap.date_field` | `WHENPOSTED` | AP filter | business: date basis |
| `ap.select_fields` | (7, see `02`) | AP page select | |
| `ap.order_by` | `RECORDNO` | AP orderby | |
| `ap.show_private` | `true` | AP options | **business rule** |
| `ap.count_basis` | `totalcount` | module 28 | |

### SQL Server (secrets in secret store)
| Config key | Default | Make source | Notes |
|---|---|---|---|
| `sql.server` | secret/config | conn 2803828 (redacted) | host\instance |
| `sql.database` | `Intacct` | proc/table names | |
| `sql.auth_mode` | _confirm_ | — | sql / windows / aad (OQ in Memory_Opti §8) |
| `sql.username` / `sql.password` | secret | — | if sql auth |
| `sql.gl_table` | `dbo.GLTable` | DeleteFromTable 135 / proc | |
| `sql.apbill_table` | `dbo.APBillTable` | DeleteFromTable 136 / proc | |
| `sql.gl_delete_date_field` | `Entry_Date` | mainflow 135 filter | ⚠️ differs from `gl.date_field` (OQ-2) |
| `sql.proc_add_gl` | `AddGLDetailBulk` | module 58 | |
| `sql.proc_add_ap` | `AddAPBillBulk` | module 56 | |
| `sql.proc_sync_links` | `[Intacct].[dbo].[SyncAPLinksToGL]` | module 70 | |
| `sql.proc_update_vendor` | `[Intacct].[dbo].[UpdateVendorInfoInGL]` | module 159 | |
| `sql.apbill_full_reload` | `true` | mainflow 136 (empty where) | full wipe vs windowed |
| `sql.load_strategy` | `transaction` | — | `transaction` \| `staging_swap` (OQ-5) |

### Reliability
| Config key | Default | Make source | Notes |
|---|---|---|---|
| `retry.count` | `3` | Break count | per I/O op |
| `retry.interval_s` | `900` | Break interval 15 (min) | Make uses 15 min; tune |
| `retry.reconcile_settle_s` | `3` | module 71 sleep | pre-reconcile pause |

### Logging (see `07`)
| Config key | Default | Make source | Notes |
|---|---|---|---|
| `log.sink` | `file` | datastore 23497 | `file`/`db`/`stdout` |
| `log.sharepoint_upload` | `false` | module 119 | optional parity (OQ-6) |
| `log.sharepoint_folder` | `/Veritas Integrations/Intaact feeds to SQL Server/Logs` | module 119 | if enabled |
| `log.file_name_pattern` | `{run_type}run_log_{MMDDYYYY}.txt` | module 119 | matches Make naming |
| `log.level` | `INFO` | — | stdlib logging level |

### Audit trail (see `12`)
| Config key | Default | Notes |
|---|---|---|
| `audit.enabled` | `true` | emit structured run/step metrics |
| `audit.sink` | `file` | `file`/`stdout` |
| `audit.dir` | `./audit` | configurable location (created if missing) |
| `audit.format` | `json` | `json` (one file/run) or `jsonl` (appended trend log) |
| `audit.file_name_pattern` | `audit_{run_type}_{run_id}.json` | `{run_id}` = `YYYYMMDD_HHMMSS` |
| `audit.jsonl_file_name` | `audit_trail.jsonl` | used when `format: jsonl` |

## 6.2 Secrets handling (NFR-4)
- Secrets (`sage.*_password`, `sql.password`, AAD tokens) resolved from env vars / `.env` / a secret
  manager — keyed by `environments[].sage_creds_key`. The repo's existing pattern is a parent-dir
  `.env` (see `tools/make_cli.py` / `.make/config.json`, both git-ignored).
- `config/config.example.yaml` contains **only** non-secret defaults + placeholders.
