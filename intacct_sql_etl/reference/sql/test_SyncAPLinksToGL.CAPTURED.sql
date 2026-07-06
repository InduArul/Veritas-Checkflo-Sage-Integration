-- CAPTURED 2026-07-06 from dbo.test_SyncAPLinksToGL (baseline BEFORE Source_Env change).
-- Joins GL<->AP on Location_ID + Bill_No(=Record_ID). Record_ID is NOT unique, and
-- Location_ID has NULLs / overlaps across companies -> add AND G.Source_Env = A.Source_Env
-- so links are stamped only from the SAME company's bill (see design doc).
CREATE PROCEDURE [dbo].[test_SyncAPLinksToGL]
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
        ON G.Location_ID = A.Location_ID
        AND G.Bill_No = A.Record_ID
    WHERE G.Entry_Date >= @StartDate
      AND G.Entry_Date <= @EndDate
END;
