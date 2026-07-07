-- NEW test_AddAPBillBulk — multi-entity keyed (OQ-31, specs/04 FR-LOAD-7).
-- Change vs CAPTURED baseline: + @Source_Env param; MERGE match on (Source_Env, Record_No);
-- writes Source_Env. Page is single-env, so @Source_Env stamps the whole batch.
-- W8/OQ-15: MERGE OUTPUTs $action -> proc returns an authoritative (inserted, updated) result set.
--   The MERGE data logic is UNCHANGED (same rows written) -> parity preserved.
ALTER PROCEDURE [dbo].[test_AddAPBillBulk]
    @json NVARCHAR(MAX),
    @Source_Env NVARCHAR(64)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @acts TABLE (act NVARCHAR(10));   -- OQ-15: capture per-row MERGE action

    ;WITH Parsed AS
    (
        SELECT
            Location_ID             = MEGAENTITYID,
            Record_No               = RECORDNO,
            Record_ID               = RECORDID,
            Stampli_Link            = STAMPLI_LINK,
            Platform_Services_Link  = PLATFORM_SERVICES_LINK,
            Vendor_ID               = VENDORID,
            Vendor_Name             = VENDORNAME,
            When_Posted             = WHENPOSTED
        FROM OPENJSON(@json)
        WITH (
            MEGAENTITYID            VARCHAR(50)   '$.MEGAENTITYID',
            RECORDNO                VARCHAR(100)  '$.RECORDNO',
            RECORDID                VARCHAR(50)   '$.RECORDID',
            STAMPLI_LINK            VARCHAR(MAX)  '$.STAMPLI_LINK',
            PLATFORM_SERVICES_LINK  VARCHAR(MAX)  '$.PLATFORM_SERVICES_LINK',
            VENDORID                VARCHAR(50)   '$.VENDORID',
            VENDORNAME              VARCHAR(MAX)  '$.VENDORNAME',
            WHENPOSTED              DATE          '$.WHENPOSTED'
        )
    )
    MERGE dbo.test_APBillTable AS target
    USING Parsed AS src
       ON target.Source_Env = @Source_Env          -- entity-scoped key
      AND target.Record_No  = src.Record_No
    WHEN MATCHED THEN
        UPDATE SET
            target.Location_ID             = src.Location_ID,
            target.Record_ID               = src.Record_ID,
            target.Stampli_Link            = src.Stampli_Link,
            target.Platform_Services_Link  = src.Platform_Services_Link,
            target.Vendor_ID               = src.Vendor_ID,
            target.Vendor_Name             = src.Vendor_Name,
            target.When_Posted             = src.When_Posted
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (
            Source_Env, Location_ID, Record_No, Record_ID, Stampli_Link,
            Platform_Services_Link, Vendor_ID, Vendor_Name, When_Posted
        )
        VALUES (
            @Source_Env, src.Location_ID, src.Record_No, src.Record_ID, src.Stampli_Link,
            src.Platform_Services_Link, src.Vendor_ID, src.Vendor_Name, src.When_Posted
        )
    OUTPUT $action INTO @acts;

    SELECT
        inserted = SUM(CASE WHEN act = 'INSERT' THEN 1 ELSE 0 END),
        updated  = SUM(CASE WHEN act = 'UPDATE' THEN 1 ELSE 0 END)
    FROM @acts;
END;
