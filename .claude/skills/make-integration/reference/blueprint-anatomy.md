# Make blueprint anatomy

All structure below is `[VERIFIED]` from real blueprint exports in `Source code/`
and `scratch/`, unless marked `[DOC]`.

## Top level
```jsonc
{
  "name": "Scenario name",
  "flow": [ <module>, <module>, ... ],
  "metadata": {
    "instant": false,                 // true for webhook/instant triggers
    "version": 1,
    "designer": { "orphans": [], "samples": { "<id>": {…saved sample data…} } },
    "scenario": {
      "dlq": false,                   // store incomplete executions?
      "dataloss": false,              // "allow data loss"
      "maxErrors": 3,
      "autoCommit": true,
      "sequential": false,
      "roundtrips": 1,
      "freshVariables": false,
      "autoCommitTriggerLast": true
    }
  }
}
```
`designer.samples` holds **saved sample bundles from past runs** — useful for
understanding shapes, but **may be stale / from a different env**. Don't treat
samples as live data.

## Module object
```jsonc
{
  "id": 177,                          // unique within scenario; identity for {{177.x}}
  "module": "microsoft-excel:listTableRows",   // "app:Action"
  "version": 2,
  "parameters": { "__IMTCONN__": 450334 },     // connection-level / static config
  "mapper": { "table": "Bank_Table", "worksheet": "Checking accounts" }, // per-run input mapping
  "filter": {                          // gate: module runs only if conditions pass
    "name": "label",
    "conditions": [ [ {"a":"{{70.`__IMTKEY__`}}","b":"TOPLEVEL","o":"text:notequal"} ] ]
  },
  "flags": { "groupBy": "{{8.col1}}" },        // aggregators only
  "onerror": [ <handler modules> ],            // error-handler route
  "routes": [ { "flow": [ <module>… ] } ],     // routers only
  "metadata": { "designer": {"x":7200,"y":1500,"name":"Location Name - Intacct"},
                "expect": [...], "interface": [...], "restore": {...} }
}
```
- **`mapper` vs `parameters`:** `mapper` = data inputs evaluated each bundle (can use
  `{{}}` expressions); `parameters` = static/connection config (e.g. `__IMTCONN__`).
- **`filter.conditions`** = OR of ANDs: outer array = OR groups, inner array = ANDed
  conditions `{a, b, o}` where `o` is an operator like `text:equal`, `text:equal:ci`,
  `text:notequal`, `number:equal`, `exist`.
- **`metadata.designer.name`** = the human label shown in the UI (great for locating a
  module the user describes by name, e.g. "Location Name - Intacct").

## References & functions `[VERIFIED examples]`
- Field ref: `{{177.values.\`4\`}}` (collection index 4), `{{9.URL}}`, `{{8.col1}}`.
- Array map: `{{179.array[].values.\`5\`}}` (pluck field 5 from every aggregated item).
- Functions seen in real blueprints: `if(cond; then; else)`, `lower()`, `trim()`,
  `sort(array; order; key)`, `ceil()`, `emptystring`.
- `[VERIFIED via make-skills]` IML uses **`;`** to separate args, **`&`/`|`** (never
  `AND`/`OR`) for logic, and **1-based** array indexing (`arr[1]`). There is **NO
  `filter()` function** — for in-memory selection use `map(array; key; [filterKey];
  [filterValues])` (**case-sensitive, raw key names**), plus `get()`, `first()`,
  `contains()`, `distinct()`, `length()`. Collection field-by-index access uses the
  literal key, e.g. `{{177.values.\`4\`}}` (0-based label, like Google Sheets columns).
  Full catalog: `make-skills:make-module-configuring/iml-expressions`.

## Flow-control / data modules
| Module | Role |
|---|---|
| `builtin:BasicRouter` | branches into `routes[].flow[]` |
| `builtin:Iterator` | **fan-out**: splits an array into one bundle per element |
| `builtin:BasicAggregator` | **this IS the "Array Aggregator"** — collapse many bundles → ONE bundle with an `array`. `parameters.feeder` = the **Source Module** (aggregation boundary). With `flags.groupBy` it instead emits one bundle per key (see below). **There is NO separate `builtin:ArrayAggregator` slug.** Aggregators do **not** forward upstream/source data — you must explicitly map the fields you want into the aggregated element. |
| `util:TextAggregator` / `util:NumericalAggregator` / `util:TableAggregator` | other aggregator types (string / number / table) |
| `util:SetVariables` / `util:GetVariables` | store/read named variables |
| `builtin:Break` | error-handler directive (see below) |

### Aggregator grouping `[VERIFIED behavior in this repo]`
`builtin:BasicAggregator` with `flags.groupBy: "{{8.col1}}"` emits **one output bundle
per distinct group key**, turning the rest of the flow into a per-group loop.
- `{{<aggId>.\`__IMTKEY__\`}}` = the current group's key.
- `{{<aggId>.\`__IMTAGGLENGTH__\`}}` = number of items in the group.
- `{{<aggId>.array}}` = the aggregated items.
A `filter` on the aggregator (e.g. `"Valid CSV"`) drops bundles before grouping.

### SetVariables scope `[VERIFIED]`
`mapper.scope`:
- `"roundtrip"` (UI label **"One cycle"**) — variable lives for the current
  cycle/iteration only.
- `"execution"` — variable persists for the whole scenario run. **Use this to cache
  reference data fetched once and read inside a loop.**

## Error-handler directives (`onerror`)
Attach an `onerror: [...]` route to a module to handle its failures.
- **`builtin:Break`** `[VERIFIED]` — `mapper {retry: bool, count: N, interval: minutes}`.
  With `retry:true` it re-attempts the module `count` times `interval` minutes apart;
  UI label is **"Automatically complete execution"** (after retries are exhausted the
  execution is committed/completed rather than rolled back → no DLQ entry).
- **`Ignore`** `[DOC]` — discard the error, continue (drop the bundle).
- **`Resume`** `[DOC]` — substitute a replacement output and continue.
- **`Rollback`** `[DOC]` — undo and stop (default behavior without a handler when
  data-loss isn't allowed → creates an incomplete execution / DLQ).
- **`Commit`** `[DOC]` — stop but keep what succeeded.

A failure caught by an attached handler shows in the UI as **"Handled error"**.
