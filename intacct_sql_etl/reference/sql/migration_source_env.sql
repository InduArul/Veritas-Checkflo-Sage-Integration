-- ============================================================================
-- MIGRATION: add Source_Env multi-entity keying to the test_ tables (OQ-31)
-- Target: dbo.test_GLTable, dbo.test_APBillTable  (TEST ONLY — prod untouched)
-- Safe/reversible: test_ objects only. Run once. See specs/04 FR-LOAD-7.
-- Order: clear tables (they will be reloaded for BOTH envs) -> add column ->
--        swap AP unique key -> (then ALTER the 4 procs, separate files) -> reload.
-- ============================================================================
SET XACT_ABORT ON;
BEGIN TRAN;

-- 1) Clear both test tables (current AP is collision-polluted from the 07-06 additive test;
--    both are reloaded cleanly for Evergreen + Veritashs after the procs are updated).
DELETE FROM dbo.test_GLTable;
DELETE FROM dbo.test_APBillTable;

-- 2) Add Source_Env to GL (plain scoping column; GL key (Record_No,Line_No) stays globally unique).
IF COL_LENGTH('dbo.test_GLTable','Source_Env') IS NULL
    ALTER TABLE dbo.test_GLTable ADD Source_Env NVARCHAR(64) NOT NULL;

-- 3) AP: drop the Record_No-only unique key, add Source_Env, add the composite unique key.
IF EXISTS (SELECT 1 FROM sys.key_constraints WHERE name='test_UQ_APBillTable_RecordNo'
           AND parent_object_id=OBJECT_ID('dbo.test_APBillTable'))
    ALTER TABLE dbo.test_APBillTable DROP CONSTRAINT test_UQ_APBillTable_RecordNo;

IF COL_LENGTH('dbo.test_APBillTable','Source_Env') IS NULL
    ALTER TABLE dbo.test_APBillTable ADD Source_Env NVARCHAR(64) NOT NULL;

IF NOT EXISTS (SELECT 1 FROM sys.key_constraints WHERE name='test_UQ_APBillTable_SrcEnv_RecordNo'
               AND parent_object_id=OBJECT_ID('dbo.test_APBillTable'))
    ALTER TABLE dbo.test_APBillTable
        ADD CONSTRAINT test_UQ_APBillTable_SrcEnv_RecordNo UNIQUE (Source_Env, Record_No);

COMMIT;
-- After this: run the 4 ALTER PROCEDURE scripts (test_Add*Bulk.NEW.sql, test_*InGL.NEW.sql),
-- then reload both envs via the orchestrator/additive loader.
