# Raw lossless extraction — Worker "Intaact feeds to SQL_daily run" (scenario 1744137)

> Reverse-engineered module-by-module from `scratch/sql_dailyrun_1744137.json` (read in full, 5377
> lines). Secrets/PII redacted. This is the working source for spec sections 02/03/04. Traceable by
> module id back to `reference/worker_dailyrun_1744137.sanitized.json`.

## 1. TRIGGER
- Module **158** `gateway:CustomWebHook`, hook **748767** label `Daily_run`, maxResults 1, instant.
- Input: `Report_start_date`, `Report_end_date` (MM/DD/YYYY strings).

## 2. EXECUTION ORDER (nesting)
```
158 webhook → 2 SetVars(Today_date=158.Report_end_date, Report_start_date=158.Report_start_date)
1  excel:listTableRows Env_data (Config.xlsx)  ← OUTER per-environment FEEDER
 3  datastore:GetRecord Sage_creds key Prod_Sage_{{1.values.`0`}}
 102 GetRecord log → 103 UpdateRecord 'Processing data for "<env>"'
 90 SetVars copy Sage creds → Sage_* roundtrip vars ; 11 GetVars them
 12 http getAPISession → sessionid
 13 BasicRouter (3 routes, all execute):
   ROUTE1 GL:
     15 http GL probe (pagesize 1) → numremaining
     122 router (success / error):
       1a (no errormessage): 17 SetVars(Total=numremaining+1, offset=0, sage_session_id)
          64 Repeater repeats=ceil(Total/2000) min1:
             20 GetVars offset; 18 http GL page (2000, offset);
             140 router length(18.data)>0:
                81 TransformToJSON(18.data) [filter statusCode==200]
                21 SetVars(offset+=2000, sql_data=replace(81.json,"'","''"))
                58 mssql:Query EXEC AddGLDetailBulk
                114 Aggregator(feeder 64)  ; 111 Get→115 Update log "<Total> GL Records added..."
       1b (errormessage): 123 Get→124 Update "Fetch GL Details error: <desc2>"
   ROUTE2 AP:
     27 http AP probe (pagesize 1) → totalcount
     125 router (success/error):
       2a: 28 SetVars(Total=totalcount, offset=0, sage_session_id)
          29 Repeater repeats=ceil(Total/2000) min1:
             30 GetVars offset; 31 http AP page (2000, offset);
             141 router length(31.data)>0:
                129 TransformToJSON(31.data) [filter statusCode==200]
                32 SetVars(offset+=2000, sql_data=replace(129.json,"'","''"))
                56 mssql:Query EXEC AddAPBillBulk
                113 Aggregator(feeder 29); 112 Get→116 Update "Fetched <Total> APBill records..."
       2b: 126 Get→127 Update "Fetch APBill Details error: <desc2>"
   ROUTE3 reconcile:
     71 FunctionSleep 3s
     70 mssql:StoredProcedure [Intacct].[dbo].[SyncAPLinksToGL] @StartDate,@EndDate
     159 mssql:StoredProcedure [Intacct].[dbo].[UpdateVendorInfoInGL] @StartDate,@EndDate
     133 Get→134 Update "Updated Vendor, Stampli links..." + 'Processing for "<env>" complete'
 110 Aggregator(feeder 1)  ← closes per-environment loop
 120 Get→137 Update "Processing complete"
 119 sharepoint:uploadAFile run-log .txt
```
- Outer loop = per environment (feeder module 1, closed by aggregator 110).
- Inner loops = GL pages (repeater 64 / agg 114), AP pages (repeater 29 / agg 113).

## 3. SAGE EXTRACTION (all http:ActionSendData)
Common: URL `{{11.Sage_URL}}` (e.g. `https://api.intacct.com/ia/xml/xmlgw.phtml`), POST,
contentType application/xml, raw body, parseResponse true, gzip true, handleErrors true. Creds in
body: senderid/sender_pass/userid/companyid/user_pass — all from datastore (module 3), redacted.

- **12 LOGIN** `getAPISession` → sessionid at `12.data.response.operation[].result[].data[].api[].sessionid[]`.
- **15 GL PROBE** object **GLDETAIL**, pagesize 1, offset 0, auth=session(12).
- **18 GL PAGE** object GLDETAIL, pagesize **2000**, offset `{{20.offset}}`, auth=`{{17.sage_session_id}}`, adds `<options><returnformat>json</returnformat></options>`.
  - GL SELECT fields: LOCATIONID, LOCATIONNAME, DEPARTMENTID, DEPARTMENTTITLE, RECORDNO, ACCOUNTNO, ACCOUNTTITLE, ENTRY_DATE, BATCH_DATE, VENDORID, VENDORNAME, LINE_NO, RECORDID, AMOUNT, PRDESCRIPTION, DESCRIPTION, SYMBOL. (Sage also returns ENTRYDESCRIPTION.)
  - GL FILTER: `BATCH_DATE >= {{2.Report_start_date}}` AND `BATCH_DATE <= {{2.Today_date}}` AND `BOOKID = ACCRUAL`.
  - GL ORDERBY: AUWHENCREATED asc, RECORDNO asc, LINE_NO asc.
- **27 AP PROBE** object **APBILL**, pagesize 1, auth=session(12). SELECT (probe, 4): MEGAENTITYID, RECORDNO, RECORDID, STAMPLI_LINK.
- **31 AP PAGE** object APBILL, pagesize 2000, offset `{{30.offset}}`, auth=`{{28.sage_session_id}}`, `<options><showprivate>true</showprivate><returnformat>json</returnformat></options>`.
  - AP SELECT (full, 7): MEGAENTITYID, RECORDNO, RECORDID, VENDORID, VENDORNAME, STAMPLI_LINK, PLATFORM_SERVICES_LINK.
  - AP FILTER: `WHENPOSTED >= {{2.Report_start_date}}` AND `WHENPOSTED <= {{2.Today_date}}`.
  - AP ORDERBY: RECORDNO asc. options showprivate true.
- NO readMore; pagination is explicit `<offset>` increments.

## 4. PAGINATION
- GL repeater 64: `repeats = if(17.Total_records_count<=2000;1;ceil(/2000))`; Total = `numremaining+1` from probe 15.
- AP repeater 29: `repeats = if(28.Total<=2000;1;ceil(/2000))`; Total = `totalcount` from probe 27.
- offset starts 0, += 2000 per page (modules 21/32). Routers 140/141 skip empty pages (length>0 & statusCode 200).

## 5. TRANSFORM
- 2: Today_date={{158.Report_end_date}}, Report_start_date={{158.Report_start_date}}.
- 90: Sage_URL/Sage_sender_id/Sage_Sender_pass/Sage_Company_ID/Sage_user_id/Sage_User_Password ← datastore rec 3 fields URL/sender_id/sender_password/company_id/user_id/user_password.
- 17: Total_records_count=sum(15...numremaining;1), offset=0, sage_session_id=12...sessionid.
- 21: offset=sum(20.offset;2000), sql_data=replace(81.json,"'","''").
- 28: Total_records_count=27...totalcount, offset=0, sage_session_id=12...sessionid.
- 32: offset=sum(30.offset;2000), sql_data=replace(129.json,"'","''").
- 81 TransformToJSON(18.data)→json (GL page string); 129 TransformToJSON(31.data)→json (AP page).
- **No per-column mapping in Make** — whole Sage JSON array (keys = SELECT field names) handed to SQL procs which map JSON→columns server-side.

## 6. SQL LOAD (conn 2803828)
- **58** mssql:Query (GL): `DECLARE @json NVARCHAR(MAX) = N'{{21.sql_data}}'; EXEC AddGLDetailBulk @json;`
- **56** mssql:Query (AP): `DECLARE @json NVARCHAR(MAX) = '{{32.sql_data}}'; EXEC AddAPBillBulk @json;`  (note: no N prefix)
- **70** StoredProcedure `[Intacct].[dbo].[SyncAPLinksToGL]` @StartDate={{158.Report_start_date}}, @EndDate={{158.Report_end_date}} (date params).
- **159** StoredProcedure `[Intacct].[dbo].[UpdateVendorInfoInGL]` @StartDate, @EndDate.
- Row deletion happens inside the bulk procs / mainflow DELETE, not here. Append-then-reconcile.

## 7. EXCEL / SHAREPOINT (conn 450334 Veritas_Sharepoint_Connection, azure)
- **1** excel:listTableRows — workbook `Veritas Integrations/Intaact feeds to SQL Server/Config.xlsx`, worksheet Sheet1, table **Env_data**; col0 = environment name (e.g. Evergreen). Drives per-env loop + datastore key `Prod_Sage_<env>`.
- **119** sharepoint:uploadAFile — folder `/Veritas Integrations/Intaact feeds to SQL Server/Logs`, filename `Dailyrun_log_{{formatDate(now;"MMDDYYYY")}}.txt`, data = full log + "End of process", conflictBehavior replace.

## 8. DATASTORES
- **1862** "Sage_creds": GetRecord key `Prod_Sage_<env>` → {URL, sender_id, sender_password[R], company_id, user_id, user_password[R]}.
- **23497** "Log_text_SQLFeeds" key "1" field Description — append log lines (103,115,124,116,127,134,137; reads 102,111,112,120,123,126,133). Messages: 'Processing data for "<env>"', "<Total> GL Records added... period start to Today", "Fetched <Total> APBill records...", error lines with description2, "Updated Vendor, Stampli links and PS links...", 'Processing for "<env>" complete', "Processing complete".

## 9. ERROR HANDLING
- builtin:Break retry=true count=3 interval=15 on modules: 2(154),1(155),12(152),15(145),18(143),27(146),31(144),58(147),56(148),70(149),119(153).
- Module 159 has NO onerror. Sage error messages handled in-flow via routers 122/125 → 123/124, 126/127.

## 10. SCENARIO METADATA
dlq true, maxErrors 3, autoCommit true, roundtrips 1, sequential false, freshVariables false, autoCommitTriggerLast true, instant true. No scheduling block (webhook worker).

## 11. KEY REBUILD NOTES
1. One login → sessionid reused for all calls. 2. Offset pagination (probe pagesize1 → ceil(count/2000) → offsets). GL count=numremaining+1, AP count=totalcount. 3. GL filter BATCH_DATE window + BOOKID=ACCRUAL; AP filter WHENPOSTED window + showprivate. 4. Load via JSON→AddGLDetailBulk/AddAPBillBulk (escape '→''). 5. Reconcile SyncAPLinksToGL then UpdateVendorInfoInGL after 3s. 6. Outer loop over environments (Excel Env_data); per-env creds Prod_Sage_<env>. 7. Retry 3×/15min on all I/O. 8. MM/DD/YYYY dates.
