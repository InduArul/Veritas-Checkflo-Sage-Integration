-- CAPTURED 2026-07-06 from dbo.test_UpdateVendorInfoInGL (baseline BEFORE Source_Env change).
-- Same GL<->AP join as SyncAPLinksToGL -> add AND G.Source_Env = A.Source_Env.
CREATE PROCEDURE [dbo].[test_UpdateVendorInfoInGL]
    @StartDate DATE,
    @EndDate DATE
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE G
    SET
        G.Vendor_ID = A.Vendor_ID,
        G.Vendor_Name = A.Vendor_Name
    FROM dbo.test_GLTable G
    INNER JOIN dbo.test_APBillTable A
        ON G.Location_ID = A.Location_ID
        AND G.Bill_No = A.Record_ID
    WHERE G.Vendor_Name IS NULL
      AND G.Entry_Date >= @StartDate
      AND G.Entry_Date <= @EndDate
END;
