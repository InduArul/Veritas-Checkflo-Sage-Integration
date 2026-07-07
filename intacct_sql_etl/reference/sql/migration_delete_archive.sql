-- W8 A3 — delete-archive tables (idempotent). Snapshot of rows removed by the delta delete-sweep,
-- so every swept row is recoverable/inspectable (audit gap found in the #12 soak review).
-- Schema-mirror of the base test_ tables + run_id + deleted_at. test_-prefixed (CLAUDE.md #3);
-- reads/writes here NEVER touch prod GLTable/APBillTable. Column order = base cols, then run_id,
-- deleted_at — matches the loader's `INSERT INTO archive SELECT *, @run_id, SYSUTCDATETIME() FROM base`.

IF OBJECT_ID('dbo.test_GL_deleted_archive', 'U') IS NULL
BEGIN
    SELECT TOP 0 * INTO dbo.test_GL_deleted_archive FROM dbo.test_GLTable;
    ALTER TABLE dbo.test_GL_deleted_archive ADD run_id NVARCHAR(64) NULL, deleted_at DATETIME2(3) NULL;
END;

IF OBJECT_ID('dbo.test_AP_deleted_archive', 'U') IS NULL
BEGIN
    SELECT TOP 0 * INTO dbo.test_AP_deleted_archive FROM dbo.test_APBillTable;
    ALTER TABLE dbo.test_AP_deleted_archive ADD run_id NVARCHAR(64) NULL, deleted_at DATETIME2(3) NULL;
END;
