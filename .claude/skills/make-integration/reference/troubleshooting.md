# Troubleshooting Make executions

## Step 1 — locate the run
```bash
python tools/make_cli.py logs <id> --limit 30        # find the execution by timestamp
python tools/make_cli.py dlqs <id>                   # incomplete (failed) executions
```
**Timezone:** logs are **UTC**. This org is US Eastern → subtract 4h (EDT) / 5h (EST)
to match a user-reported local time. (e.g. "11:43 AM" = `15:43Z` in June.)

## Step 2 — read the status `[VERIFIED]`
| `status` | Meaning |
|---|---|
| `1` | Success |
| `2` | Completed **with handled errors** (retries/handlers fired but run finished) |
| (in DLQ) | Incomplete execution — rolled back / stored as failure |

A `status:2` or a `status:1` with retries means **errors happened but were handled**.
The per-module error bundles for a *successful* run are **NOT in the API** — open the
execution in the Make UI inspector and click the module's red marker (Input/Output/
**Error** tabs). For *incomplete* executions use `dlq <dlqId>` to get the bundle.

## Make-side error codes seen `[VERIFIED]`
| Code | Meaning / cause |
|---|---|
| Cloudflare `1010` | banned User-Agent — send a browser UA |
| `SC403` "Permission denied" | wrong scope, or **org id used where teamId expected** |
| `SC400` "Value doesn't match pattern" | malformed param — e.g. executionId must be the **hex only**, not the full imtId |
| module `[503] ConnectionError` | transient Microsoft/Graph server error (see below) |

## Microsoft 365 / Graph throttling (Excel & SharePoint) `[MS-LEARN]`
This is the usual cause of intermittent failures in scenarios that call
Excel/SharePoint **inside a loop**.

- **HTTP 429 "TooManyRequests"** (Graph) — a throttling threshold was exceeded.
  **HTTP 503 "Server Too Busy"** (SharePoint Online) — temporary load spike.
  **Both include a `Retry-After` header (seconds).**
  Sources: learn.microsoft.com/graph/throttling ; learn.microsoft.com/sharepoint/dev/general-development/how-to-avoid-getting-throttled-or-blocked-in-sharepoint-online
- **Correct handling:** wait `Retry-After` seconds, retry; if still 429/503 keep using
  the recommended delay; if no header, use **exponential backoff**. Aggressive retrying
  (without honoring the delay) *extends* the throttle and can escalate to a hard block.
- **Limits:** Excel = 5,000 req/10s per app (all tenants), 1,500 req/10s per app per
  tenant; SharePoint = 3,000 requests / 5 min per user. (learn.microsoft.com/graph/throttling-limits)
- **Reduce call volume (official guidance):** fewer operations per request; lower call
  frequency; avoid polling (use delta/change notifications); batch (Graph JSON batch
  max **20**, each sub-request throttled individually); run heavy jobs off-peak.
  (learn.microsoft.com/onedrive/developer/rest-api/concepts/scan-guidance ; /graph/json-batching)

### Decision tree for an Excel/SharePoint failure in a loop
1. Is it `429` or `503` with the module **inside an iterator/aggregated loop**?
   → It's volume/throttling. **Best fix: fetch the static data once before the loop**
   (cache in an `execution`-scoped variable) and look up in-memory. See the loop-lookup
   playbook in `review-checklist.md`.
2. Is it a one-off `503` with `Retry-After`? → A `builtin:Break` retry handler with a
   sensible `interval` is acceptable; honor the delay.
3. Is it `429` repeating and escalating? → You're retrying too hard; increase interval
   / reduce concurrency / cut call volume.

## The CSV "Number of columns on line N does not match header" DLQ `[VERIFIED context]`
Seen repeatedly on `Upload CSV to Checkflo v0.1`. This is a `csv:ParseCSV` failure: an
input row has a different column count than the header (often an unescaped
comma/quote/newline in a field, or a ragged source file). Inspect the offending line in
the `dlq <id>` bundle; fix by quoting fields / setting the parser to be tolerant, not by
retrying.
