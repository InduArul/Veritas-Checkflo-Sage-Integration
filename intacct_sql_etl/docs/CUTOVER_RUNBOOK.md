# Cutover Runbook — Make → Python Intacct SQL ETL (DRAFT, pre‑approval)

**Status:** DRAFT for review. **Nothing here executes on prod until you approve + schedule a window.**
Owner (acting DBA): automation. Relates to gates #7/#11/#12/#13, OQ‑31.

The migration replaces the Make "Intaact feeds to SQL" family with the Python ETL, writing to the
**same prod tables** (`Intacct.dbo.GLTable`, `dbo.APBillTable`) that consumers already read. Make stays
live and untouched until the final flip, and is kept (disabled) for rollback.

---

## 0. Pre‑cutover gate checklist (ALL must be green)
- [ ] #1 clean delete‑sweep, #2 delta==full‑refresh, #4 checksum, #6 date‑basis — **PASS in test** ✅
- [ ] #5 monthly, #7 multi‑entity — **PASS in test** ✅
- [ ] #8 failure alerting **live** (IT: enable SMTP AUTH on the mailbox *or* switch to Graph sendMail)
- [ ] #9 cron staged, #10 failure drills passed, #11 rotation runbook in place
- [ ] #12 **parallel‑run vs Make reconciled clean for several cycles** (the go/no‑go evidence)
- [ ] All prod entities enumerated + configured (currently Evergreen + Veritashs; confirm no 3rd)
- [ ] Sign‑off: **Yosef** (business) + you (technical)

---

## 1. Prod schema migration (the DBA change)
Mirror the `test_` changes onto prod. **Adds are safe & non‑breaking** (Make's procs don't reference
the new columns); the **proc swap** is the only part needing a maintenance window.

### 1a. Additive columns — LOW RISK (can pre‑stage ahead of the window)
```sql
-- APBill gets When_Posted (posting date, enables windowed AP sweep) + Source_Env
IF COL_LENGTH('dbo.APBillTable','When_Posted') IS NULL
    ALTER TABLE dbo.APBillTable ADD When_Posted DATE NULL;         -- nullable now; backfilled in 1c
IF COL_LENGTH('dbo.APBillTable','Source_Env') IS NULL
    ALTER TABLE dbo.APBillTable ADD Source_Env NVARCHAR(64) NULL;
-- GL gets Source_Env only (per decision: When_Posted is AP-only)
IF COL_LENGTH('dbo.GLTable','Source_Env') IS NULL
    ALTER TABLE dbo.GLTable ADD Source_Env NVARCHAR(64) NULL;
```
> Make continues running normally with these nullable columns present (it simply won't populate them).

### 1b. Proc swap — NEEDS A WINDOW (Make must be paused for this step)
Apply the `test_`‑proven proc bodies to the **prod** procs (drop the `test_` prefix):
`AddAPBillBulk` (+`@Source_Env`, MERGE on `(Source_Env,Record_No)`, write `When_Posted`),
`AddGLDetailBulk` (+`@Source_Env`), `SyncAPLinksToGL` / `UpdateVendorInfoInGL` (+`AND G.Source_Env=A.Source_Env`).
Source of truth: `reference/sql/*.NEW.sql` (retarget `test_*` → prod names).

### 1c. Backfill + key promotion — NEEDS A WINDOW
```sql
-- stamp existing rows so the new NOT NULL + composite key can be enforced
UPDATE dbo.APBillTable SET Source_Env = '<Env>' WHERE Source_Env IS NULL;   -- per the current owner
UPDATE dbo.GLTable     SET Source_Env = '<Env>' WHERE Source_Env IS NULL;
-- When_Posted backfill: re-extract from Sage (WHENPOSTED) OR one-time proc run over history
-- then enforce:
ALTER TABLE dbo.APBillTable ALTER COLUMN Source_Env NVARCHAR(64) NOT NULL;
ALTER TABLE dbo.GLTable     ALTER COLUMN Source_Env NVARCHAR(64) NOT NULL;
-- swap AP key Record_No -> (Source_Env, Record_No) (find/drop the existing PK/unique first)
ALTER TABLE dbo.APBillTable ADD CONSTRAINT UQ_APBillTable_SrcEnv_RecordNo UNIQUE (Source_Env, Record_No);
```
> ⚠️ Cross‑company `Record_No` collision: until the composite key is in place, a multi‑entity load can
> overwrite (the exact bug we fixed in test). Do 1b + 1c **together** in one window.

### 1d. Rollback (schema)
Additive columns are harmless — leave them. To revert procs: re‑apply the **captured** baseline bodies
(`reference/sql/*.CAPTURED.sql`, retargeted to prod). Re‑enable Make. Prod returns to pre‑cutover behavior.

---

## 2. Config for prod cutover
- `run.test_mode: false` (writes real tables) — **this is the one‑way switch**; gated by `assert_test_targets`
  + the `prod_write_guard` hook, so flipping it is deliberate.
- `sql.gl_table: dbo.GLTable`, `apbill_table: dbo.APBillTable`, procs = prod names.
- All prod entities enabled with their `sage_creds_key`.
- **VERIFY the prod procs' bodies target prod tables** before the first real run (the 07‑02 incident:
  a proc body referencing the wrong table bypasses the write guard). Re‑run the proc‑isolation check.

## 3. Cutover sequence (the flip)
1. Confirm §0 checklist green; announce the window.
2. Pause the Make scenarios (do **not** delete — keep for rollback).
3. Apply prod schema (§1b/§1c) in the window.
4. **First real run = reversible**: run the Python ETL once (daily upsert, no sweep) → reconcile the
   result vs the last-known-good Make snapshot. If mismatch → rollback (§1d), investigate.
5. If clean: enable the Python cron schedule (`run-etl.sh`), leave Make scenarios **disabled**.
6. Watch the first daily + first sweep + first month‑end closely; alerting must be live (#8).

## 4. Rollback (operational)
At any point: disable the Python cron, re‑enable Make scenarios, revert procs (§1d). Because the load is
idempotent (MERGE) and Make full‑reloads, the tables converge back to Make's output on its next run.

## 5. Post‑cutover
- Keep Make disabled (not deleted) for one full cycle as a safety net.
- Decommission Make once several clean Python cycles + a month‑end have passed.
- Revisit OQ‑33 (bounded parallel monthly) as a fast‑follow optimization.
