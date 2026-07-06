-- NEW test_AddGLDetailBulk — writes Source_Env (OQ-31, specs/04 FR-LOAD-7).
-- Change vs CAPTURED baseline: + @Source_Env param; GL key (Record_No,Line_No) unchanged
-- (globally unique); Source_Env written on INSERT and set on UPDATE.
ALTER PROCEDURE [dbo].[test_AddGLDetailBulk]
    @json NVARCHAR(MAX),
    @Source_Env NVARCHAR(64)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    ;WITH Parsed AS
    (
        SELECT
            Location_ID      = LOCATIONID,
            Location_Name    = LOCATIONNAME,
            Dept_ID          = DEPARTMENTID,
            Dept_Name        = DEPARTMENTTITLE,
            Record_No        = RECORDNO,
            Account_No       = ACCOUNTNO,
            Account_Title    = ACCOUNTTITLE,
            Entry_Date       = ENTRY_DATE,
            GL_Posting_Date  = BATCH_DATE,
            Vendor_ID        = VENDORID,
            Vendor_Name      = VENDORNAME,
            Line_No          = LINE_NO,
            Bill_No          = RECORDID,
            Base_Amount      = AMOUNT,
            Description      = PRDESCRIPTION,
            Memo             = DESCRIPTION,
            Journal          = SYMBOL
        FROM OPENJSON(@json)
        WITH (
            LOCATIONID        NVARCHAR(50)      '$.LOCATIONID',
            LOCATIONNAME      NVARCHAR(MAX)     '$.LOCATIONNAME',
            DEPARTMENTID      NVARCHAR(50)      '$.DEPARTMENTID',
            DEPARTMENTTITLE   NVARCHAR(MAX)     '$.DEPARTMENTTITLE',
            RECORDNO          VARCHAR(100)      '$.RECORDNO',
            ACCOUNTNO         NVARCHAR(MAX)     '$.ACCOUNTNO',
            ACCOUNTTITLE      NVARCHAR(MAX)     '$.ACCOUNTTITLE',
            ENTRY_DATE        DATE              '$.ENTRY_DATE',
            BATCH_DATE        DATE              '$.BATCH_DATE',
            VENDORID          NVARCHAR(50)      '$.VENDORID',
            VENDORNAME        NVARCHAR(MAX)     '$.VENDORNAME',
            LINE_NO           NVARCHAR(50)      '$.LINE_NO',
            RECORDID          NVARCHAR(MAX)     '$.RECORDID',
            AMOUNT            NVARCHAR(50)      '$.AMOUNT',
            PRDESCRIPTION     NVARCHAR(MAX)     '$.PRDESCRIPTION',
            DESCRIPTION       NVARCHAR(MAX)     '$.DESCRIPTION',
            SYMBOL            NVARCHAR(50)      '$.SYMBOL'
        )
    )
    MERGE dbo.test_GLTable AS target
    USING Parsed AS src
       ON target.Record_No = src.Record_No
      AND target.Line_No   = src.Line_No
    WHEN MATCHED THEN
        UPDATE SET
            target.Source_Env       = @Source_Env,
            target.Location_ID      = src.Location_ID,
            target.Location_Name    = src.Location_Name,
            target.Dept_ID          = src.Dept_ID,
            target.Dept_Name        = src.Dept_Name,
            target.Account_No       = src.Account_No,
            target.Account_Title    = src.Account_Title,
            target.Entry_Date       = src.Entry_Date,
            target.GL_Posting_Date  = src.GL_Posting_Date,
            target.Vendor_ID        = src.Vendor_ID,
            target.Vendor_Name      = src.Vendor_Name,
            target.Bill_No          = src.Bill_No,
            target.Base_Amount      = src.Base_Amount,
            target.Description      = src.Description,
            target.Memo             = src.Memo,
            target.Journal          = src.Journal
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (
            Source_Env, Location_ID, Location_Name, Dept_ID, Dept_Name, Record_No, Account_No,
            Account_Title, Entry_Date, GL_Posting_Date, Vendor_ID, Vendor_Name,
            Line_No, Bill_No, Base_Amount, Description, Memo, Journal
        )
        VALUES (
            @Source_Env, src.Location_ID, src.Location_Name, src.Dept_ID, src.Dept_Name, src.Record_No,
            src.Account_No, src.Account_Title, src.Entry_Date, src.GL_Posting_Date,
            src.Vendor_ID, src.Vendor_Name, src.Line_No, src.Bill_No, src.Base_Amount,
            src.Description, src.Memo, src.Journal
        );
END;
