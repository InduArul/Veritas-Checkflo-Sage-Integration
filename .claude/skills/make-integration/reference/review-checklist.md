# Blueprint-change review checklist

Run this against **any** proposed blueprint edit before recommending a `push`.
A change is "ready" only when every applicable item is ✅ or justified.

## A. Safety & process
- [ ] **A1** Change was made on a `pull`ed copy in `scratch/`, not hand-typed blind.
- [ ] **A2** `qa <file>` returns **0 errors**.
- [ ] **A3** `qa-diff <old> <new>` reviewed; every added/removed/retyped module is intentional.
- [ ] **A4** No live `push` without explicit user `--yes`; a `.make/backups/` rollback will exist.

## B. Structural integrity
- [ ] **B1** Every module has a unique `id` and a valid `module` type. (qa enforces.)
- [ ] **B2** **No dangling references:** every `{{id.field}}` points at a module that still
      exists *and runs before* the reference. Removing/renumbering a module that others
      reference is a break.
- [ ] **B3** Router `routes[].flow[]` nesting preserved; modules stay in the correct branch.
- [ ] **B4** Webhook wiring intact (trigger hook ids and inter-scenario `http:ActionSendData`
      → next scenario's webhook URLs unchanged unless that's the point of the change).
- [ ] **B5** `parameters.__IMTCONN__` (connections) preserved on moved/cloned modules.

## C. Loop / data-flow correctness
- [ ] **C1** **Run-once vs per-iteration:** anything meant to run once is placed *before*
      the fan-out (iterator / `groupBy` aggregator), not inside the loop.
- [ ] **C2** Variable **scope** matches lifetime: data cached for use inside a loop is
      `execution`-scoped, not `roundtrip`/"one cycle".
- [ ] **C3** Aggregator/iterator semantics unchanged where downstream depends on
      `__IMTKEY__` / `__IMTAGGLENGTH__` / `.array`.
- [ ] **C4** In-memory replacement reproduces the **exact** match logic of the call it
      replaces (same fields, same operators incl. case-insensitivity/trim).

## D. Resilience
- [ ] **D1** External calls (HTTP/Excel/SharePoint/Intacct) that remain in loops have an
      `onerror` handler or a justification.
- [ ] **D2** Retry handlers honor backoff (`interval` ≥ a few min for 429/503; don't retry
      tight). Removing a retry handler is only OK if the call it guarded is gone.
- [ ] **D3** Error logging/counters the business relies on (e.g. `No_of_files_process_failed`,
      SharePoint Log writes) are preserved or consciously dropped.

## E. Behavior preservation
- [ ] **E1** Output shape unchanged (same columns/fields/order downstream consumers expect).
- [ ] **E2** Filters that skip rows (e.g. `TOPLEVEL`, petty cash) are preserved.
- [ ] **E3** Operation count / credit impact considered (fewer is better; note the delta).
- [ ] **E4** Idempotency / no double-processing introduced.

---

## Playbook: "loop lookup → fetch once" (the 429/503 fix)
**Symptom:** an Excel/SharePoint (or other static-reference) module sits inside an
iterator or `groupBy` aggregator and is called once per item; intermittent 429/503.

**Refactor:**
1. **Add** a one-time fetch of the reference table in a part of the flow that runs
   **once** (before the fan-out). NOTE: `listTableRows` (and most "list/search"
   modules) emit **one bundle per row** — N bundles.
2. **Add** a `builtin:BasicAggregator` ("Array Aggregator") immediately after the fetch
   with **Source Module = the fetch** and **no Group By**, to **collapse the N
   row-bundles into one array AND re-converge the flow** — otherwise every downstream
   module runs N times. Aggregators don't forward source data, so **explicitly map the
   row fields** into the aggregated element, preserving the `values` collection shape so
   existing `{{id.values.\`N\`}}` references still resolve (C4/B2).
3. **Add** a `util:SetVariables` with `scope:"execution"` to cache that array
   (e.g. `Bank_Table_all = {{<agg>.array}}`).
4. **Replace** the in-loop external call with in-memory selection. PREFER a
   `builtin:Iterator` over the cached array feeding the **existing aggregator/filter**,
   so the original match logic is reproduced EXACTLY (C4). Avoid collapsing it into a
   single `map()` expression if the original match is case-insensitive or spans multiple
   fields — IML `map()` is **case-sensitive / single-key** and there is **no `filter()`
   function**, so an expression would silently change matching semantics.
5. **Remove** the now-dead `onerror` retry handler on the deleted call (D2), but keep any
   business-critical logging it did (D3) — relocate if needed. Add a small retry handler
   to the NEW one-time fetch (D1).
6. Re-check C1–C4, E1–E2. Confirm op-count drop (E3).

**Net target:** N external calls/run → 1.

> **Bundle-mechanics rule:** any "list/search/iterate" module fans out to N bundles
> (an *implicit iterator*); to use the result as a single value or to stop the flow
> multiplying, you MUST aggregate back to one bundle first with a `builtin:BasicAggregator`
> (the "Array Aggregator"). Confirmed by `make-skills` (`bundles.md`, `aggregations.md`).
