-- NEW test_SyncAPLinksToGL — entity-scoped join (OQ-31, specs/04 FR-LOAD-7).
-- Change vs CAPTURED baseline: + AND G.Source_Env = A.Source_Env. Record_ID is NOT unique,
-- so with two companies' AP in one table this prevents stamping a GL row from the wrong
-- company's bill. Signature unchanged (loader run_reconcile call stays the same).
ALTER PROCEDURE [dbo].[test_SyncAPLinksToGL]
    @StartDate DATE,
    @EndDate DATE
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE G
    SET
        G.Stampli_Link = A.Stampli_Link,
        G.Platform_Services_Link = A.Platform_Services_Link
    FROM test_GLTable G
    INNER JOIN test_APBillTable A
        ON G.Source_Env = A.Source_Env          -- entity-scoped (Record_ID not unique)
        AND G.Location_ID = A.Location_ID
        AND G.Bill_No = A.Record_ID
    WHERE G.Entry_Date >= @StartDate
      AND G.Entry_Date <= @EndDate
END;
