# Leak A — Target Design: eliminate the per-check webhook fan-out

**Goal:** remove the cross-scenario webhook plumbing in the per-check processing chain
with **0% functional change**. Grounded in the live inter-scenario contract extracted from
the `baseline/leakA_2026-06-20/` blueprints + the golden-run bundles.

---

## 1. Current state (the leak)

Part2 (`Main`) iterates each **check** in a batch via `BasicFeeder` (item module `104`).
For every check it makes **synchronous HTTP calls to four separate scenarios**:

| Part2 module | Calls | Sends (already in Part2's bundle) | Returns (`WebhookRespond`) |
|---|---|---|---|
| `155` | Sage session (Sage API, not a sub-scenario) | `922.Sage_*` creds, `922.Sage_URL` | `637.sage_session_id`, `No_of_checks_in_the_batch` |
| `965` | **Part3 Vendor** | `104.*` check fields, `637.sage_session_id`, `922.Sage_*` | `vendor_id`, `Total_check_amount`, `accum_invoice_amount`, `stop_check_processing`, `json` |
| `923` | **Part4 NegChecks** | `104.CheckNumber`, `637.*`, `922.*`, `974.Total_check_amount` | `accum_invoice_amount`, `stop_check_processing` |
| `929` | **Part5 Loop** | `104.*`, `637.*`, `922.*`, `923.data.accum_invoice_amount` | `accum_invoice_amount`, `route1..4`, `stop_check_processing` |
| `962` | **Part6 Posting** | `104.*`, `637.*`, `922.Financial_Entity`, `922.*` | *(status only — empty body)* |
| `930` | **Part7 Reporting** | batch summary | *(status only)* |

**Plumbing cost per check** (pure overhead, no business logic), for the Part3→4→5 chain:
3 × `http:ActionSendData` + 3 × `gateway:CustomWebHook` trigger + 3 × `gateway:WebhookRespond`
= **9 operations/check** consumed just moving control between scenarios.
Plus the data-store round-trips (Leak B) used to share batch-scoped state.

---

## 2. Proof: all cross-scenario state can move in-bundle

Two classes of state cross the boundary today. Both have a direct in-bundle equivalent:

### (a) Inputs — already present at the call site
Every `{{104.*}}`, `{{637.*}}`, `{{922.*}}` reference Part2 sends to a sub-scenario is
**already a live value in Part2's own bundle**. If the sub-scenario's modules run *inside*
Part2's loop, those references resolve directly — **no HTTP, no re-hydration**.

### (b) Outputs — small scalars/JSON returned via WebhookRespond
| Returned value | Today | In-bundle equivalent |
|---|---|---|
| `vendor_id` | Part3 `WebhookRespond` 967 | `util:SetVariable` (roundtrip) after the inlined Vendor logic |
| `Total_check_amount` | Part3 967 | roundtrip variable |
| `accum_invoice_amount` | Part3/4/5 | a single roundtrip variable, updated in place as the chain progresses |
| `stop_check_processing` | Part3/4/5 | roundtrip flag variable (same short-circuit semantics) |
| `route1..4`, `json` | Part5 935 / Part3 925 | roundtrip variables / direct mapping |

### (c) Batch-scoped data-store keys (Leak B, eliminated for free)
`Total_value_of_checks_from_checkflo`, `value_of_payment_posted_for_a_batch`, `Email_Text`,
and the `Temp_var`/indicator records exist **only because the sub-scenarios are separate
executions**. Part2 already owns this state for the batch; once the sub-logic is in Part2's
execution, these become roundtrip variables and the `datastore:GetRecord`/`UpdateRecord`
pairs that currently shuttle them are deleted (no behavior change — same values, same order).

> **Conclusion:** there is no state that *requires* a separate execution. The webhooks are
> a structural artifact, not a functional dependency.

---

## 3. Target options

### Option A (recommended) — inline Part3 + Part4 + Part5 into Part2's check loop
Move the Vendor → NegChecks → Invoice-loop logic into Part2's `BasicFeeder` (104) as
sequential sub-flows. Keep **Part6 (Posting) separate** (financial isolation / idempotency)
and **Part7 (Reporting) separate** (batch-level, fires once).

- **Eliminates** all 9 plumbing ops/check for the 3→1 chain **and** the inter-stage
  data-store round-trips.
- **Est. savings:** ~9 plumbing ops + several data-store ops per check × ~4,900 checks
  ≈ **45–60K ops/cycle** (on top of Leak B overlap). Largest single win.
- **Risk:** Part2 grows to ~150 modules and one execution now does the full per-check
  chain → must validate against the **40-min execution-time limit** for the largest batch
  (current Part2-only batch = 772 s for 13 checks; inlining adds the sub-scenario work but
  removes HTTP wait). **Staging load test (Checklist §4) is mandatory before go-live.**

### Option B (lower-risk fallback) — single merged `Part_345_CheckProcessing` sub-scenario
Combine Part3+4+5 into ONE sub-scenario; Part2 calls it **once** per check instead of 3×.

- Eliminates 2 of 3 hops (~6 plumbing ops/check) and the Part4↔Part5 data-store handoff.
- Keeps Part2 a clean orchestrator; smaller blast radius; trivial rollback.
- **Est. savings:** ~30–40K ops/cycle. Banks most of the win at lower risk.

**Recommendation:** ship **Option B first** (fast, low-risk, easy parity-test + rollback),
measure in Staging, then do **Option A** as phase 2 once execution-time headroom is proven.

---

## 4. Target flow (Option B)

```
Part2 (Main) — BasicFeeder: loop each CHECK (104)
  155  Sage session  (unchanged)
  →  HTTP → Part_345_CheckProcessing   (ONE call; was 3: Part3,4,5)
        in : 104.* check, 637.sage_session_id, 922.* creds/batch
        out: vendor_id, accum_invoice_amount, stop_check_processing, route1..4, json
  →  (existing variance/router logic, unchanged)
  →  HTTP → Part6 Posting   (unchanged)
  →  HTTP → Part7 Reporting (unchanged)

Part_345_CheckProcessing (NEW, = Part3 ⨁ Part4 ⨁ Part5 inlined, webhooks removed between them)
  CustomWebHook (in)  →  [Part3 Vendor modules]  →  [Part4 NegChecks modules]
                      →  [Part5 Invoice-loop modules]  →  WebhookRespond (out)
  - accum_invoice_amount / stop_check_processing carried as roundtrip variables
    (replaces the Part4→Part5 datastore handoff)
```

Every retained module keeps its **exact** `parameters`/`mapper`/`filter`/`flags`. Only the
**inter-stage plumbing** (2 webhook triggers, 2 responds, 2 HTTP calls, the datastore
handoff pair) is removed.

---

## 5. How the QA gate applies to a merge

`parity_diff.py` matches by module id (or `--by-name`). For a merge, run it **per source
scenario** against the merged blueprint:

```bash
# every Part3 module must appear unchanged in the merged scenario
python tools/parity_diff.py baseline/leakA_2026-06-20/Part3_Vendor.blueprint.json merged.json --by-name
python tools/parity_diff.py baseline/leakA_2026-06-20/Part4_NegChecks.blueprint.json merged.json --by-name
python tools/parity_diff.py baseline/leakA_2026-06-20/Part5_Loop.blueprint.json merged.json --by-name
```

Interpretation:
- **HIGH (parameter/mapper/filter drift)** → must be **zero**. Any blank→default (the
  `limit=10` class) trips here. Blocks deploy.
- **MEDIUM "MODULE REMOVED"** → expected **only** for the intentionally-removed plumbing
  (the `gateway:CustomWebHook`, `gateway:WebhookRespond`, the Part2 `http:ActionSendData`
  calls, and the datastore handoff pair). Each must be explicitly accounted for in the
  sign-off; anything else is a real regression.

Then the full **QA_Structural_Refactor_Checklist.md** (static + Staging parallel-run vs the
golden bundles) gates go-live.

---

## 6. Migration steps (per Checklist)
1. Build `Part_345_CheckProcessing` by composing Part3+4+5 flows; replace the Part4→Part5
   datastore handoff with roundtrip variables; remove the two internal webhook hops.
2. Repoint Part2 module `965` to the new webhook; delete Part2 modules `923` and `929`
   (the now-merged calls); rewire `929`'s downstream consumers to the single response.
3. Static gate: `parity_diff.py` (per-source, as above) + `make_cli.py gate`.
4. Staging: parallel-run the golden batch (OLD Part2+3+4+5 vs NEW Part2+Part_345); compare
   field-by-field against `scratch/golden_run_2026-06-20/` bundles. Confirm ops reduction.
5. Sign-off, deploy with old scenarios kept inactive-but-intact for rollback.

---

## 7. Also fix in the same pass (surfaced during baseline capture)
Part2 DLQ shows a recurring **"Validation failed — Missing value of required parameter
'id'"** (since 2026-05-29). Failed runs still consume ops + DLQ storage. Root-cause and fix
under the same refactor + parity gate.
