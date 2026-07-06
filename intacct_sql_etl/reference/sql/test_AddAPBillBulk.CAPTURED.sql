-- CAPTURED 2026-07-06 from dbo.test_AddAPBillBulk (baseline BEFORE Source_Env change).
-- NOTE: MERGE matches on Record_No ONLY -> cross-company Record_No collisions overwrite
-- (the multi-entity defect). When_Posted already present (OQ-26). See design:
-- docs/design/multi_entity_source_env_design.md
CREATE PROCEDURE [dbo].[test_AddAPBillBulk]
    @json NVARCHAR(MAX)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    ;WITH Parsed AS
    (
        SELECT
            Location_ID             = MEGAENTITYID,
            Record_No               = RECORDNO,              -- <-- UNIQUE KEY (per company only!)
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
       ON target.Record_No = src.Record_No      -- match on Record_No only  <-- COLLISION BUG
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
            Location_ID, Record_No, Record_ID, Stampli_Link,
            Platform_Services_Link, Vendor_ID, Vendor_Name, When_Posted
        )
        VALUES (
            src.Location_ID, src.Record_No, src.Record_ID, src.Stampli_Link,
            src.Platform_Services_Link, src.Vendor_ID, src.Vendor_Name, src.When_Posted
        );
END;
