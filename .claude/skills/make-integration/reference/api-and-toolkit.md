# Make REST API + `make_cli.py` toolkit

## Connection basics `[VERIFIED]`
- **Base URL:** `https://{zone}.make.com/api/v2` — `zone` is the subdomain in your
  Make account URL (here: `us2`).
- **Auth header:** `Authorization: Token {API_TOKEN}` plus `Accept: application/json`.
- **Cloudflare gotcha:** Make is behind Cloudflare, which **bans the default
  `Python-urllib` User-Agent** → HTTP 403, error `1010` `browser_signature_banned`.
  Always send a browser-like `User-Agent`. (The toolkit does this.)
- **Tokens:** created in Make → *Profile → API / MCP access → Add token*, with scopes.

## Scopes `[DOC]` (make.com docs not re-fetched — confirm exact names)
Relevant scopes for this repo's work: `scenarios:read`, `scenarios:write`,
`dlqs:read`, and a scenario-logs read scope. `/users/me` works with any valid token
(no special scope) — good for an auth smoke test.

## Team vs Organization `[VERIFIED]`
- `GET /organizations` → `{organizations:[{id,name,zone,timezoneId}]}`. (Here org id `1903574`.)
- `GET /teams?organizationId={org}` → `{teams:[{id,name,organizationId}]}`. (Here team id `33002`.)
- **`GET /scenarios` requires `teamId`** (NOT organizationId). Passing the org id in
  the `teamId` slot → `403 {"code":"SC403","message":"Permission denied"}`. This was
  the real cause of an early failure here — the `.env` had the org id in the team slot.

## Endpoints used by the toolkit `[VERIFIED unless noted]`
| Purpose | Method + path | Notes |
|---|---|---|
| Auth check | `GET /users/me` | returns user; no special scope |
| List scenarios | `GET /scenarios?teamId={team}&pg[limit]=N` | `{scenarios:[{id,name,isActive}], pg:{}}` |
| Scenario metadata | `GET /scenarios/{id}` | |
| Get blueprint | `GET /scenarios/{id}/blueprint` | returns `{response:{blueprint:{…}}}` — unwrap `response.blueprint` |
| Update blueprint | `PATCH /scenarios/{id}` | body `{"blueprint": "<stringified JSON>"}` — blueprint is a **JSON string**, not object. `[DOC]` |
| List run logs | `GET /scenarios/{id}/logs?pg[limit]=N&pg[sortDir]=desc` | `{scenarioLogs:[{imtId,status,operations,timestamp}]}` |
| One execution | `GET /scenarios/{id}/logs/{executionId}` | **`executionId` = the 32-char HEX only**, not the full `imtId` (`timestamp_hex`). Passing the full imtId → `SC400 "Value doesn't match pattern"`. Returns summary only (no per-module bundles). |
| Incomplete execs (DLQ) | `GET /dlqs?scenarioId={id}` | `{dlqs:[{id,reason,created,size,resolved,retry,attempts}]}` — has bundle detail |
| One DLQ bundle | `GET /dlqs/{dlqId}` | |
| Run on demand | `POST /scenarios/{id}/run` | body `{"responsive":true,"data":{…}}` `[DOC]` |

### Execution log fields `[VERIFIED]`
`imtId` (`{epochms}_{hex}`), `id` (hex), `status` (**1=success, 2=completed w/ handled
errors**), `operations`, `duration` (ms), `transfer` (bytes), `centicredits`,
`eventType` (`EXECUTION_END`), `isReplayable`, `teamId`, `scenarioId`, `scenarioName`.

### Pagination `[VERIFIED pg[limit], pg[sortDir]; others DOC]`
`pg[limit]`, `pg[offset]`, `pg[sortBy]`, `pg[sortDir]` (`asc`/`desc`).

## `make_cli.py` commands
| Command | Does |
|---|---|
| `doctor` | GET /users/me — prints zone/user/team/org |
| `scenarios [--limit N] [--json]` | list scenarios for the team |
| `get <id>` | scenario metadata |
| `pull <id> [--out FILE]` | download blueprint (unwraps response.blueprint) |
| `logs <id> [--limit N] [--json]` | recent execution logs |
| `log <id> <executionId>` | one execution's summary (use HEX id) |
| `dlqs <id> [--limit N] [--json]` | incomplete executions for a scenario |
| `dlq <dlqId>` | one incomplete execution bundle |
| `run <id> [--data FILE]` | run once (live QA) |
| `qa <file>` | static structural QA of a local blueprint |
| `qa-diff <old> <new>` | module add/remove/retype diff (traverses routers + onerror) |
| `gate <file> [--baseline OLD]` | deep QA gate: reference integrity, aggregator feeders, invalid slugs, variable usage, diff vs baseline, + manual checklist. Exit 2 on errors. |
| `push <id> <file> --yes [--force]` | update live; auto-backs up first; QA-gated |

### Config resolution (in `make_cli.py`)
1. `.env` files searched: `$MAKE_ENV_FILE`, `<repo>/.env`, `<repo>/.make/.env`,
   `<parent>/.env`. Parser accepts `KEY=value` **and** `KEY:"value"`, strips quotes/BOM/`export`.
2. `.make/config.json` (gitignored).
3. Env vars / file keys: `MAKE_API_TOKEN|MAKE_VERITAS_API_TOKEN`,
   `MAKE_ZONE|MAKE_VERITAS_ZONE` (a trailing `.make.com` is stripped),
   `MAKE_TEAM_ID|MAKE_VERITAS_TEAM_ID`, `MAKE_ORG_ID|MAKE_VERITAS_ORG_ID`.

### Safety in `push`
GETs and backs up the current live blueprint to `.make/backups/{id}-{ts}.json`,
runs local QA (aborts on errors unless `--force`), and refuses to PATCH without
`--yes`. Roll back by pushing the backup file.
