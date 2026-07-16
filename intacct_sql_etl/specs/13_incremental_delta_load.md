# 13 — Incremental (Delta / CDC) Load — Watermark Upsert + Delete-Sweep

**Status:** DRAFT for sign-off (design agreed with user 2026-07-02; no code until approved).
**Supersedes for normal operation:** the full-refresh *bulk window wipe* in `05_orchestration_and_scheduling.md` §5.3 (`DeleteFromTable` GL windowed / APBill full). That blunt delete-then-reload is **removed**; see §13.9 for what §05 keeps vs drops.
**Motivation:** the twice-daily job currently deletes ~4 months of rows and re-inserts everything (~600k+ GL rows/run) even though almost nothing changed. This is the biggest source of wasted runtime, SQL write I/O, lock time, and Sage API load. It also could not detect deletions cleanly (root cause of the 1,908 stale prod rows found 2026-07-02).

---

## 13.1 Goal (plain statement)

Each run pulls from Intacct **only records that are new or changed since the last successful run**, applies them by **upsert** (add new / update changed, never duplicate), and — **once per night** — runs a lightweight **delete-sweep** to remove SQL rows that no longer exist in Intacct. Applies to **both** the daily and the monthly run.

---

## 13.2 Confirmed facts (live probe 2026-07-02)

- `GLDETAIL` exposes `WHENMODIFIED` (datetime), `WHENCREATED`, `MODIFIED`, `AUWHENCREATED` (71 fields total).
- `APBILL` exposes `WHENMODIFIED` (datetime), `WHENCREATED`, `WHENPOSTED` (100 fields total).
- ⇒ `WHENMODIFIED` is the **change-tracking field** for both objects, and being a **datetime** it also provides the closed-time cutoff required by `05` §5 / the current-day cutoff decision.
- Natural keys (from the bulk-proc MERGE, `specs/04`): **GL = `RECORDNO` + `LINE_NO`**; **AP = `RECORDNO`**. `RECORDNO` is globally unique in `GLTable` (verified: 5,082,065 distinct = row count).

---

## 13.3 The watermark ("bookmark")

A per-`(environment, object)` timestamp remembering the last point we successfully pulled up to.

**Control table** (test-mode name `test_etl_watermark`; prod name `etl_watermark` — config-driven, `test_`-guarded in dev):

| Column | Type | Meaning |
|---|---|---|
| `environment` | varchar | e.g. `Evergreen` |
| `object` | varchar | `GL` or `AP` |
| `watermark` | datetime2 | last successfully processed `WHENMODIFIED` cutoff |
| `updated_at` | datetime2 | when this row was last advanced |

Rules:
- The watermark is **read at the start** of an upsert and **advanced only after a fully successful, committed run** (transactional with the load). A failed/degraded run leaves it untouched, so the next run re-covers the same ground (safe, idempotent).
- Next watermark value = the run's **cutoff** (§13.6), not `max(WHENMODIFIED)` seen — there are no records with `WHENMODIFIED ≤ cutoff` left unprocessed by definition.
- Stored in SQL (not a file) so it is crash-safe and survives redeploys.

---

## 13.4 Operation A — Upsert (delta)

Runs on **every** scheduled invocation (daily intraday runs, the nightly run, and the monthly run).

1. Read `watermark` for `(env, object)`.
2. Query Intacct with filter **on the change field only**:
   `WHENMODIFIED > (watermark − grace)` **AND** `WHENMODIFIED <= cutoff`.
   **No posting-date/window filter on the upsert** — this captures *every* change regardless of posting date and makes the shared watermark unambiguous (see §13.8 for why).
3. Page through results (existing offset pagination, §02) and **MERGE-upsert** by natural key (existing bulk procs, `specs/04`). MERGE = add-if-new / update-if-changed; re-fetching a record is harmless.
4. Capture `MERGE ... OUTPUT $action` → **inserted vs updated** counts for the audit trail (`specs/12`) and the daily report (`specs/`-notifier). This also closes OQ-15.
5. On full success, **advance the watermark to `cutoff`** inside the same transaction as the load.

**Grace / lookback** (`sage.watermark_grace`, **= 1 hour**, confirmed 2026-07-02): re-scan a 1h margin before the watermark to catch records committed slightly late or with clock skew. Overlap is harmless because MERGE is idempotent.

---

## 13.5 Operation B — Delete-sweep (stateless)

Removes SQL rows that were **deleted in Intacct**. `WHENMODIFIED` deltas can never see deletions (deleted rows stop appearing), so this is required for correctness.

**Period rule (client-confirmed 2026-07-02, OQ-20) — the fact that makes windowed sweeping provably safe:** hard deletes happen **only in OPEN (recent) accounting periods**. In a **CLOSED** period, GL/AP are **never hard-deleted — only reversed**, and a reversal is a *new* row (own `WHENMODIFIED`) that the delta **upsert** already captures. Therefore the delete-sweep only needs to span the **currently-open period(s)** — a recent window, never all history — and old/closed data is **never** hard-deleted, so windowed sweep + accumulate (§13.9) can never wrongly drop history. (Watch-item: the sweep window must always cover every open period; the daily window must do so, unless a period stays open longer than it spans.)

**Nightly window narrowed 4→3 months (2026-07-14):** `daily_lookback_months` 3→2 (current + 2 prior). Evidence: across every nightly sweep 07-08…07-14, deletions landed only in current + 1 prior (a thin tail in month-2); **month-3 (April) had 0 deletions on every night**, while the full 4-month sweep was reaching the 3600s wall-clock cap (reaped 07-14). Open question to client (pending): how many periods stay open at once / max open span / can a closed period reopen — the answer confirms 3 months is always safe. Mitigation until answered: a wider weekly backstop sweep is the intended fast-follow.

1. Pull **keys only** from Intacct for the sweep window — `RECORDNO` (+ `LINE_NO` for GL) — one/two fields, large page size ⇒ cheap.
2. Compare to the SQL keys **within the same window**.
3. **Delete** SQL rows whose keys are **absent** from Intacct. Never inserts/updates (that is Operation A's job) — so it cannot race the upsert.

**Sweep window field (confirmed 2026-07-02, B3 / OQ-2):** the window is defined on **`ENTRY_DATE`** (Sage) ↔ **`Entry_Date`** (SQL column) on both sides of the comparison — matches the current project's proven behaviour. (This is the same field `gl_delete_date_field` already points at.)

**AP sweep = windowed on a date column, or SKIPPED (revised 2026-07-02).** `APBillTable` has **no date column** (columns: `Location_ID, Record_No, Record_ID, Stampli_Link, Platform_Services_Link, Vendor_ID, Vendor_Name`). A *full-table* compare is **unsafe** — it cannot distinguish "deleted in Intacct" from "aged out / archived / not returned," so in a later year it would delete historical bills the accumulate rule (§13.9) says to keep (e.g. in 2027 it would wipe all 2024 AP rows Intacct no longer returns). Therefore:
- If `sql.apbill_sweep_date_field` (a real date column, e.g. `When_Posted`) is configured, AP sweep is **windowed on it `[start,end]` — exactly like GL** on `Entry_Date` — so data outside the window is never touched (accumulate respected). Requires the DBA to add the column + have `test_AddAPBillBulk` populate it from `WHENPOSTED` (→ OQ-26).
- If it is **not** configured (default), AP delete-sweep is **SKIPPED** (logged) — never a destructive full-table compare. AP deletions simply aren't reconciled until the column exists.

**Mass-delete guard (both GL and AP).** `sql.sweep_max_delete_fraction` (default 0.5): if a sweep would delete more than this fraction of the rows it scanned, it **raises `SweepGuardError`** → the env transaction rolls back and the run is flagged/alerted. Defense-in-depth against any mis-scoped/partial key pull silently wiping a window.

**Entity scoping (`Source_Env`) — added 2026-07-06 (OQ-31, `04` FR-LOAD-7).** `GLTable`/`APBillTable` are **shared across all Sage companies**, and AP `Record_No` (Sage's per-company PK) **collides across companies**. Every sweep step therefore scopes to the env being processed: the window key-read **and** the delete both carry `AND Source_Env = ?`, so an env's sweep only ever compares/deletes **its own** rows — a sibling company's rows are never candidates for deletion (they are not "missing," they simply belong to another env). Same scoping applies to the full-refresh `wipe_window` (GL window-DELETE + AP delete) and the upsert (procs carry the env via a `@Source_Env` parameter). See `04` FR-LOAD-7 for the key/schema model and the accepted divergence from Make.

- **Does not use the watermark** (stateless point-in-time comparison).
- Sweep **window/scope differs by run type** (§13.7).

---

## 13.6 The cutoff (closed-time bound)

Every run computes an **upper bound** `cutoff` = "now, rounded back to a closed boundary" (e.g. now truncated to the minute/hour), so we never page the still-open current minute (removes offset-drift risk).

- `cutoff` is a **datetime** on `WHENMODIFIED` (date-only `BATCH_DATE` could not express a time cutoff — that is why `WHENMODIFIED` is used).
- **Timezone (confirmed 2026-07-02):** `WHENMODIFIED` is in the **Intacct company timezone**. The cutoff and stored watermark are computed/compared in the **company tz** (round-trip the exact string/format Sage returns) to be boundary-safe. Verify the exact string format during build (still OQ-16b).

---

## 13.7 Run types — same engine, different sweep scope

Both run types use **identical** upsert logic (§13.4). They differ **only** in the delete-sweep scope:

| Run | Trigger | Upsert (delta) | Delete-sweep | Scope |
|---|---|---|---|---|
| **Daily intraday** | 08:00, 17:30 | ✅ | ❌ (skipped) | — |
| **Nightly** | 01:00 | ✅ | ✅ | **3-month** window (`daily_lookback_months: 2`; narrowed from 4 on 07-14) |
| **Monthly** | 1st of month | ✅ | ✅ (**wide**) | **Jan-1-last-year → month-end** (deep reconcile) |

- **Delete-sweep runs once per night** regardless of how many intraday upserts occurred (user decision 2026-07-02). The nightly job is **delete-sweep + upsert** together (user decision).
- The **monthly** wide sweep is the deep periodic delete/drift catch; on the 1st it **supersedes** the narrow nightly sweep (it is a superset).
- `run_type` derivation (`dates.derive_run_type`, §05 §5.2) is retained but now selects the **sweep scope**, not a wipe.

**Example schedule (cron; times configurable — OQ-17):**
```
08:00  intacct-etl --mode upsert
17:30  intacct-etl --mode upsert
01:00  intacct-etl --mode sweep-upsert        # delete-sweep (4-mo) + upsert
1st ~01:00  intacct-etl --run-type monthly --mode sweep-upsert   # wide sweep + upsert
```

---

## 13.8 Why no posting-window filter on the upsert (fall-through avoidance)

If daily and monthly shared one watermark **and** the upsert were also filtered by the posting window, a record **modified today but posted outside the daily window** would be skipped by the daily run *and* the watermark would advance past it — so the monthly run would never see it either (**silent loss**). Filtering the upsert on `WHENMODIFIED` **alone** eliminates this: the watermark captures all changes continuously; the window only ever scopes the **delete-sweep**.

Consequence: a change to a very old record is still applied. Given the tables **accumulate full history** (§13.9), that is correct — updating an existing old row, or inserting one that legitimately exists in Intacct, is desired.

---

## 13.9 History / retention (accumulate mode)

- **Decision (user 2026-07-02):** tables **accumulate** — data older than the window is **left in place**, never wiped. (Observed: prod `GLTable` already spans 2024→2026 / ~5M rows.)
- **No purge** for now. A **retention-purge is built as an OFF-by-default config toggle** (`sql.retention.purge_before` or similar) so the project team can enable trimming later with no rework. → user action item (OQ-18).

---

## 13.10 One-time seed (initial backfill)

The first run for an `(env, object)` has no watermark. Seed = **one full backfill** over the desired history window, then set the initial watermark to the seed cutoff. Subsequent runs are small deltas. (The existing full-window extract path is reused for the seed only.)

**Confirmed 2026-07-02:** the **live/prod** DB already contains data from **2024-01-01** onward — so **prod needs no seed** at cutover (the watermark is simply initialised to the cutover cutoff; deltas take over from there). Only the **`test_` shadow tables** need a seed backfill; seed them **from 2024-01-01 → now** to mirror live, so parity comparisons (`specs/11`) are apples-to-apples.

---

## 13.11 Completeness & failure rules (correctness gate)

- **Upsert completeness:** rows loaded must equal the delta query's probe total; on shortfall, mid-page Sage error, or any exception → **do not advance the watermark**, roll back, and alert (ties to `07`/notifier). No partial commit that silently advances state.
- **Delete-sweep safety:** if the Intacct key pull is incomplete/errored, **abort the sweep** (never delete based on a partial key set) and alert.
- **Idempotent recovery:** because the watermark only advances on success and MERGE/sweep are idempotent, the recovery action for any failure is simply "re-run."

---

## 13.12 Parity validation (before trusting delta)

During shadow: for a given `(env, window)`, a **delta run followed by a delete-sweep must reproduce the same table state as a full-refresh** over that window (row counts + key set + column checksums per `specs/11`). Delta is not trusted for cutover until this equivalence holds across several cycles.

**Offline proof (done):** `tests/integration/test_delta_parity.py` proves the property model-based — delta(upsert)+sweep from a *dirty* table (stale row, stale amount, missing row) converges to the exact same key set **and values** as a full refresh, and is idempotent on re-run. Live parity vs Make/Sage still required before cutover.

---

## 13.13 Benefits (why this is the priority CR)

- Normal-day write volume: from ~1.2M row-ops/run (delete + re-insert) to **a few changed rows + a cheap nightly key scan**.
- Removes the long-held write-lock / half-empty-table window of the full-refresh (resolves the atomicity/locking concern raised in the architecture review — no more DELETE-4-months-then-reload).
- Correctly handles **new + changed + deleted** (the full-refresh missed deletes beyond its wipe; pure delta misses deletes entirely — this design covers both).
- Cuts Sage API paging dramatically.

---

## 13.14 Impact on existing code (for the build wave, post-approval)

- `sage_query`: add `WHENMODIFIED`-based delta filter builder; add a keys-only query variant for the sweep.
- `orchestrator`: introduce **modes** (`upsert`, `sweep`, `sweep-upsert`); remove the unconditional `wipe_window` from the normal path (retain for seed/backfill only); advance watermark transactionally on success.
- new `watermark` module + control-table DDL (`test_`-guarded).
- new **delete-sweep** step (key diff → scoped delete; identifiers config-driven + validated, `MERGE`/`DELETE` bound-param dates).
- `cli`: `--mode`, keep `--run-type`/`--start`/`--end` for seed/backfill.
- `audit`/notifier: inserted/updated/deleted counts (`specs/12`, OQ-15).
- `dates`: add `month_chunks_dt(start_dt, end_dt)` (datetime month-tiling for the upsert axis); `orchestrator._process_environment_delta` loops month-chunks for **both** upsert and sweep so peak memory ≤ one chunk (§13.16, NFR-MEM-1); `run.chunk_months` config.
- `deploy/run-etl.sh`: hardened rootless run-wrapper (flock + cgroup `MemoryMax` + `timeout`) — operational memory guard (§13.16).
- TDD: failing tests first for watermark advance/no-advance, delta filter, sweep key-diff, fall-through, parity-vs-full-refresh, **and chunk tiling + per-chunk interleaving (memory-safety)**.

---

## 13.15 Open questions (new)

- **OQ-16 [user/DBA] `WHENMODIFIED` timezone. — RESOLVED 2026-07-02: company timezone.** (OQ-16b: confirm the exact datetime string format during build for a boundary-safe round-trip.)
- **OQ-17 [user] Schedule times.** Confirm 08:00 / 17:30 upserts + 01:00 nightly (sweep+upsert) + 1st-of-month monthly (wide) — and the timezone/active weekdays (also OQ-8).
- **OQ-18 [user] Retention purge.** (User action item) decide if/when to purge history older than the window; toggle stays OFF until confirmed.
- **OQ-19 [user] Seed depth. — RESOLVED 2026-07-02:** prod already has data from 2024-01-01 (no prod seed); seed the **`test_`** tables from **2024-01-01 → now** to mirror live for parity.
- **OQ-20 [DBA] GL/AP hard-deletes. — RESOLVED 2026-07-02 (client):** hard delete only in OPEN periods; CLOSED periods are reversal-only (reversals = new rows caught by upsert). ⇒ sweep only needs to cover open period(s); accumulate of old data is provably safe. See §13.5 period rule.
- **OQ-21 [user] `grace` interval. — RESOLVED 2026-07-02: 1 hour.**

---

## 13.16 Bounded-memory chunked processing (memory-safety NFR)

**Incident (root cause, 2026-07-02 EOD):** a wide-window `sweep-upsert` on the shared VM (`VTSAzPLW01`, 15 GB RAM, **swap = 0**, co-resident with the stampli Docker stack) drove the box to thrash and starved `sshd` ("VM overload"). Root cause: the delta path pulls its **entire** window in one pass — `sage_client._fetch()` accumulates every page into a single in-memory `list` (`records.extend(page)`) before anything is loaded. A wide run (nightly/monthly sweep ≈ **600k+ GL keys**, or a far-behind / seed upsert) materialises the whole set at once. With no swap on a shared host, the spike is fatal to the whole VM, not just the job.

**Requirement (NFR-MEM-1): peak in-memory record set ≤ one chunk, independent of total window span.** Both delta operations MUST process their window in **calendar-month sub-chunks** — extract → apply → **release** per chunk — exactly as the full-refresh path already does (`orchestrator._process_environment` loops `dates.month_chunks`; specs/05 §5.5). No delta code path may hold more than one chunk's records live.

**Two chunk axes (they differ — do not merge into one loop):**
- **Upsert** chunks on the **`WHENMODIFIED` datetime range** `(since, cutoff]` (§13.4/§13.6). Sub-intervals share boundaries at calendar-month starts; with the builder's strict `> lower` / `<= upper` filter the partition is exact (no gap, no double-count). Per chunk: `fetch_gl_delta`/`fetch_ap_delta` for the sub-interval → MERGE upsert (`commit=False`) → drop the reference.
- **Delete-sweep** chunks on the **date window** `[start, end]` (Entry_Date / `apbill_sweep_date_field`), via `dates.month_chunks`. Per month: `fetch_gl_keys`/`fetch_ap_keys` for that month → `sweep_gl`/`sweep_ap` **windowed to that same month** → drop the reference. Sub-windows partition the window, so per-chunk "delete-missing" is identical to whole-window "delete-missing" (parity preserved, §13.12).

**Watermark under chunking (preserves the §13.11 completeness gate):** the upsert advances the watermark to **`cutoff`** for an object **only if every chunk for that object fetched cleanly**. Any chunk error on an object holds that object's watermark back (and the single per-env transaction still rolls back on a hard failure). Sweep chunks keep the existing per-chunk mass-delete guard (`sweep_max_delete_fraction`) and the empty-key-pull skip.

**Config:** `run.chunk_months` (default **1**) sets the sub-chunk size for both axes; `1` = one calendar month. It only bounds memory/paging — it does **not** change which rows are processed (chunked result == single-window result, asserted by test).

**Defense-in-depth (operational, not a substitute for chunking):** `deploy/run-etl.sh` wraps every scheduled/manual run in a `flock` overlap guard + a rootless cgroup `MemoryMax` (systemd-run `--user`, default 3 GB, `MemorySwapMax=0`, `ulimit -v` fallback) + a wall-clock `timeout`. A run that still tries to over-allocate is OOM-killed **in its own cgroup** (rc 137) — the VM, stampli, and sshd are unaffected — and the wrapper logs a `MEMORY CAP HIT` line for the notifier. Chunking makes the cap never fire in normal operation; the cap makes a future regression survivable.
