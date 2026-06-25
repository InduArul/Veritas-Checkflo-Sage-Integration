# reference/sql/ — Stored procedure source (TO CAPTURE — OQ-1)

The Make pipeline delegates all column mapping + reconciliation to four SQL Server stored procedures.
Their bodies are **required** for a complete spec. Capture them from the `Intacct` database and commit
here (they are business logic, not secrets):

```sql
SELECT OBJECT_DEFINITION(OBJECT_ID('dbo.AddGLDetailBulk'));
SELECT OBJECT_DEFINITION(OBJECT_ID('dbo.AddAPBillBulk'));
SELECT OBJECT_DEFINITION(OBJECT_ID('dbo.SyncAPLinksToGL'));
SELECT OBJECT_DEFINITION(OBJECT_ID('dbo.UpdateVendorInfoInGL'));
```

Also capture the target table DDL for the column contract + parity checks (spec 09):

```sql
-- column names/types feed the GL/AP field→column mapping verification
EXEC sp_help 'dbo.GLTable';
EXEC sp_help 'dbo.APBillTable';
```

Expected files: `AddGLDetailBulk.sql`, `AddAPBillBulk.sql`, `SyncAPLinksToGL.sql`,
`UpdateVendorInfoInGL.sql`, `GLTable.ddl.sql`, `APBillTable.ddl.sql`.
