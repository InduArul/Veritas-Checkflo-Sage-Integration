# 02 — Sage Intacct Extraction (Worker)

**Source of truth:** workers `daily run` (1744137) & `monthly run` (1744512) — identical extraction
logic. Module ids below reference `reference/_extraction_dailyrun_1744137.md`.

All Sage calls go to the **Intacct XML Gateway** over HTTP POST.

- **Endpoint:** `Sage_URL` (per-environment, from creds store) — e.g. `https://api.intacct.com/ia/xml/xmlgw.phtml`
- **Content-Type:** `application/xml`; body is raw XML. No HTTP basic auth (auth is in the XML body).
- **Response handling:** request `gzip`; treat non-2xx/3xx as errors (`handleErrors`); parse response.
- **DTD version:** `3.0`. `<uniqueid>false</uniqueid>`, `<includewhitespace>false</includewhitespace>`.

> The full verbatim XML for each call is preserved in `reference/_extraction_dailyrun_1744137.md` §3
> and in the sanitized blueprint `reference/worker_dailyrun_1744137.sanitized.json`. This spec
> documents the contract + parameters; the rebuild must reproduce the XML exactly (field lists,
> filters, orderby, options).

---

## FR-EXT-1 — Authentication (module 12)

One `getAPISession` login **per environment per run**; the returned `sessionid` is reused for **all**
subsequent calls (probe + page, GL + AP) in that environment.

- Operation: `<getAPISession/>` inside `<authentication><login>` with `userid`, `companyid`,
  `password` (CDATA), empty `locationid`; control block carries `senderid` + sender `password` (CDATA).
- Session id path in response:
  `data.response.operation[].result[].data[].api[].sessionid[]`.

**Rebuild:** authenticate once, cache the session id for the environment's run. Credentials come from
config / secret store (see `06`), never hardcoded. (Future option: token-based session reuse across
the whole run — see OQ-7.)

---

## FR-EXT-2 — GL Detail extraction (modules 15 probe, 18 page)

**Object:** `GLDETAIL`.

**SELECT fields (exact order):** `LOCATIONID, LOCATIONNAME, DEPARTMENTID, DEPARTMENTTITLE, RECORDNO,
ACCOUNTNO, ACCOUNTTITLE, ENTRY_DATE, BATCH_DATE, VENDORID, VENDORNAME, LINE_NO, RECORDID, AMOUNT,
PRDESCRIPTION, DESCRIPTION, SYMBOL`.
*(Sage additionally returns `ENTRYDESCRIPTION` in responses; the consuming proc tolerates the extra key.)*

**FILTER (`<and>`):**
- `BATCH_DATE >= {start_date}`
- `BATCH_DATE <= {end_date}`  *(end_date = the window's `Today_date`/`Report_end_date`)*
- `BOOKID = 'ACCRUAL'`   ← **business rule, must be configurable** (see `03`/`06`)

**ORDER BY:** `AUWHENCREATED` asc, `RECORDNO` asc, `LINE_NO` asc.

**Probe (module 15):** `pagesize=1, offset=0`, no `returnformat` (XML response). Used only to read the
record count from `_attributes.numremaining`.

**Page fetch (module 18):** `pagesize=2000`, `offset={current}`, `<options><returnformat>json</returnformat></options>`. Returns a flat JSON array in `data`.

**Count → page loop:** `total = numremaining + 1` (note: **GL uses `numremaining + 1`**).
`pages = (total <= 2000) ? 1 : ceil(total / 2000)`. Offsets: `0, 2000, 4000, …`.

---

## FR-EXT-3 — AP Bill extraction (modules 27 probe, 31 page)

**Object:** `APBILL`.

**SELECT fields:**
- **Probe (27):** `MEGAENTITYID, RECORDNO, RECORDID, STAMPLI_LINK` (4 fields).
- **Page (31):** `MEGAENTITYID, RECORDNO, RECORDID, VENDORID, VENDORNAME, STAMPLI_LINK, PLATFORM_SERVICES_LINK` (7 fields).
  ⚠️ The page query selects **3 more fields** than the probe (VENDORID, VENDORNAME, PLATFORM_SERVICES_LINK). The rebuild's loaded AP record set = the **7-field** page set.

**FILTER (`<and>`):**
- `WHENPOSTED >= {start_date}`
- `WHENPOSTED <= {end_date}`

**ORDER BY:** `RECORDNO` asc.

**OPTIONS:** `<showprivate>true</showprivate>` (probe + page); page adds `<returnformat>json</returnformat>`.

**Probe (module 27):** `pagesize=1, offset=0`.
**Page fetch (module 31):** `pagesize=2000`, `offset={current}`, JSON return.

**Count → page loop:** `total = totalcount` (note: **AP uses `_attributes.totalcount` directly**, *not* numremaining+1). `pages = (total <= 2000) ? 1 : ceil(total / 2000)`. Offsets `0, 2000, …`.

---

## FR-EXT-4 — Pagination model (general)

- **Offset/pagesize**, NOT Intacct `readMore`/`resultId`. Each page re-issues the full `<query>` with
  an incremented `<offset>`. Page size **2000** (configurable — see `06`).
- A pagesize-1 **probe** precedes the loop purely to learn the total count.
- Per-page guards (Make routers 140/141 + statusCode filters): only process a page if
  `length(data) > 0` **and** the call returned HTTP `statusCode == 200`. Empty/failed pages are skipped.
- Pages are **not** accumulated in memory — each page is loaded to SQL immediately, then the next page
  is fetched. (The rebuild may batch differently, but must not hold the whole dataset if memory is a
  concern; streaming page-by-page is the proven pattern.)

---

## FR-EXT-5 — Sage error handling (in-flow, modules 122/125)

After each **probe**, branch on whether the response contains an `errormessage`:
- **No error** → proceed to the page loop.
- **Error present** → skip loading that object and log
  `… Fetch {GL|APBill} Details error: {result.errormessage.error.description2}` (do **not** abort the
  whole run; the other object/route still proceeds). See `07_logging_error_handling.md`.

This is distinct from transport/retry errors (FR-ERR-* in `07`).

---

## FR-EXT-6 — Two independent pipelines

GL and AP extraction are **independent** (Make router 13 fans them out in parallel) and both must
complete before the reconciliation step (`04` FR-LOAD-3). In Python they may run sequentially or
concurrently, but the reconcile procs run **after both** GL and AP loads for the window.

---

## Parameterization (see `06_configuration_parameters.md`)
Configurable: endpoint URL, credentials (per env), `page_size` (2000), GL `BOOKID` filter value
(`ACCRUAL`), GL/AP date filter field names (`BATCH_DATE`/`WHENPOSTED`), select-field lists, orderby,
DTD version, gzip, request timeout, retry policy.
