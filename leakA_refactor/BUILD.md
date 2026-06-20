# Leak A Refactor — Option B Build (per-check webhook fan-out → single sub-scenario)

**Status:** structurally complete, statically verified end-to-end. **Not yet Staging-validated.**
**Baseline:** `baseline/leakA_2026-06-20/` (live blueprints, drift-checked == `Source code/`).

## What this builds
Replaces Part2's **three** per-check sub-scenario calls (Part3 Vendor → Part4 NegChecks →
Part5 Loop) with **one** merged sub-scenario `Part_345`, eliminating the webhook plumbing
(2 triggers + 2 responds + 2 HTTP hops inside the chain, plus `923`/`929` HTTP + `355`
datastore from Part2). Per the agreed scope: **internal data-stores kept as-is** (Leak B deferred).

Two artifacts in `candidates/`:
- `Part_345_CheckProcessing.blueprint.json` — merged Part3⨁Part4⨁Part5 (133 → 135 modules
  incl. final-respond layer), single webhook trigger, 4 mutually-exclusive responds.
- `Part2_Main_WIRED.blueprint.json` — Part2 calling `Part_345` once; `923/355/929` deleted;
  `923/929.data → 965.data` repointed.

## Reproduce
```bash
python leakA_refactor/compose_p345.py   # builds candidates/Part_345_CheckProcessing.blueprint.json
python leakA_refactor/wire_part2.py     # builds candidates/Part2_Main_WIRED.blueprint.json
```
Both read only from `baseline/leakA_2026-06-20/`. Deterministic — same inputs → identical output.

## How the merge works
- **Renumber** Part4 (+10000) / Part5 (+20000) to collision-free ids.
- **Trigger union:** single trigger `918` (= Part3's); Part4/Part5 trigger refs rewired —
  ambient fields → `918`; inter-stage fields → producer modules.
- **Glue carried (not dropped):** `[974]` SetVariables (repointed to producers), `[975]`
  json-transform + stop gate, `[355]` datastore `temp_var` reset (repointed `926`→`10926`).
- **Seams:** Part4 spliced where Part3 responded (`967`); Part5 spliced where Part4 responded
  (`10927`). Aggregator collapses the neg-invoice loop → Part5 runs **once per check**.
- **Final-respond (union schema):** every path returns
  `{Vendor_id, Total_check_amount, Simple_array_of_json, route1..4, stop_check_processing,
  accum_invoice_amt/amount/amout}` — the exact set Part2 consumes. Responds: `20935` (success),
  `32001` (stop after Vendor), `32002` (stop after NegChecks), `971` (fallback).
- **Part2 wiring:** `975`'s existing `stop≠1` filter (now reading the final stop via
  `974`←`965.data`) preserves the original "skip post-call on stop" behavior — no new gate added.

## Verification (static)
| Gate | Result |
|---|---|
| Integrity (Part_345) | 0 dangling refs · single trigger · all 4 responds carry all 11 union fields |
| Parity Part3/4/5 portions | retained modules byte-identical; only intended renumber + documented rewires; the one `HIGH` is the deliberate `971` body normalization |
| Parity Part2 | `HIGH=4` all intended (`965` URL + `923/929.data→965.data`); `MEDIUM=3` = the 3 deletions; 0 unexpected |
| Integration contract | every field Part2 reads from the response is provided by `Part_345` (`missing: none`) |
| `make_cli qa` | 0 errors / 0 warnings on both |
| Secret scan | clean |

## Ops impact (estimate)
~6–9 plumbing ops/check removed × ~4,900 checks/cycle ≈ **~30–44K ops/cycle**, 0% functional change.

## Deployment (remaining — all via Staging first)
1. Create `Part_345` in **Staging** from `candidates/Part_345_CheckProcessing.blueprint.json`;
   copy its webhook URL into `Part2_Main_WIRED` module `965` (replace
   `REPLACE_WITH_PART_345_WEBHOOK`).
2. **Parallel-run** the golden batch (OLD Part2+3+4+5 vs NEW Part2+Part_345); compare
   field-by-field against `scratch/golden_run_2026-06-20/` bundles. **Top focus:** `[962]`
   Sage payment posting, the neg-invoice **datastore lifecycle** (now one execution), the
   stop-exit responds (`32001/32002`).
3. Run the full `docs/QA_Structural_Refactor_Checklist.md` (Staging section).
4. Deploy to production; keep old scenarios **inactive-but-intact** for one-click rollback.

## Known forward items
- **Leak B (datastores)** intentionally untouched — separate pass.
- The merged scenario's behavior under **concurrent checks** (neg-invoice store scoping/cleanup)
  is the #1 runtime risk to validate in Staging.
