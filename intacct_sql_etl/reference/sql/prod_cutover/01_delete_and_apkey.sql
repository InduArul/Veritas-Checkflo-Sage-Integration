-- PROD CUTOVER 2026-07-15 — destructive DDL/DML (executed step-by-step via pyodbc, batched).
-- Precondition: full backups exist (GLTable_prebutover_20260715, APBillTable_prebutover_20260715);
--               Make scenarios PAUSED; additive columns already added (Source_Env, When_Posted).
-- Decision (user): keep 2024 GL data (Source_Env stays NULL for it); reseed 2025-01-01 -> now.

-- 1) GL: delete 2025-01-01 onward (KEEP 2024). Batched to bound the transaction log.
--    (~3,869,572 rows). Executed as: WHILE loop DELETE TOP (100000) ... Entry_Date >= '2025-01-01'.
DELETE FROM dbo.GLTable WHERE Entry_Date >= '2025-01-01';   -- (batched at runtime)

-- 2) AP: delete ALL (APBillTable has no date column; Make full-wipes+reloads it each run,
--    so it holds no 2024 history to preserve). ~29,648 rows.
DELETE FROM dbo.APBillTable;

-- 3) AP key promotion (on the now-empty table):
ALTER TABLE dbo.APBillTable DROP CONSTRAINT UQ_APBillTable_RecordNo;          -- old single-col key
ALTER TABLE dbo.APBillTable ALTER COLUMN Source_Env NVARCHAR(64) NOT NULL;    -- safe: table empty
ALTER TABLE dbo.APBillTable
    ADD CONSTRAINT UQ_APBillTable_SrcEnv_RecordNo UNIQUE (Source_Env, Record_No);

-- GL: Source_Env stays NULLABLE (2024 rows keep NULL). GL key remains (Record_No, Line_No).
-- Rollback: restore from *_prebutover_20260715; re-apply *.PROD_CAPTURED.sql procs; re-enable Make.
