-- NEW test_UpdateVendorInfoInGL — entity-scoped join (OQ-31, specs/04 FR-LOAD-7).
-- Change vs CAPTURED baseline: + AND G.Source_Env = A.Source_Env. Signature unchanged.
ALTER PROCEDURE [dbo].[test_UpdateVendorInfoInGL]
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
        ON G.Source_Env = A.Source_Env          -- entity-scoped (Record_ID not unique)
        AND G.Location_ID = A.Location_ID
        AND G.Bill_No = A.Record_ID
    WHERE G.Vendor_Name IS NULL
      AND G.Entry_Date >= @StartDate
      AND G.Entry_Date <= @EndDate
END;
