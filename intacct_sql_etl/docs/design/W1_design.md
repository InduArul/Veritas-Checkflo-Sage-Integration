# W1 Design — GL vertical slice (extract → transform → load → dry-run reconcile)

Traces to: specs/02 (GL extract), specs/03 (transform), specs/04 (load), specs/11 (reconcile).
Every external boundary (Sage HTTP, SQL DB) is **injected** so the slice is deterministic and
offline-testable with fakes. Real wire adapters (HTTP/XML, pyodbc) are thin and integration-tested
once Sage/SQL creds exist (OQ-11) — their parsing is not unit-tested here (no live payloads).

## `sage_query.py` — XML builders (pure; specs/02)
- `fmt_date(d) -> "MM/DD/YYYY"`.
- `build_login_xml(creds) -> str` — getAPISession (creds: sender_id, sender_password, user_id,
  user_password, company_id).
- `build_gl_query_xml(*, session_id, start, end, offset, page_size, fields, date_field="BATCH_DATE",
  book_filter="ACCRUAL", order_by, return_json) -> str` — object `GLDETAIL`; SELECT = `fields` in order;
  filter `date_field >= start AND date_field <= end AND BOOKID = book_filter`; `<orderby>`; optional
  `<options><returnformat>json</returnformat></options>`; `<pagesize>`, `<offset>`.
  - Probe = page_size 1, return_json False, offset 0. Page = page_size 2000, return_json True.

## `transform.py` — page encoding (pure; specs/03)
- `encode_page(records: list[dict]) -> str` — compact JSON (`separators=(",",":")`, `ensure_ascii=False`).
  No quote-escaping: the JSON is passed to the proc as a **bound parameter** (not string-interpolated),
  removing Make's `replace('→'')` hack and the injection surface (specs/03 FR-TR-2).

## `sage_client.py` — extract orchestration (transport injected)
- Transport contract: `transport(xml: str) -> dict`
  - login → `{"session_id": str}` or `{"error": str}`
  - query → `{"status_code": int, "total": int|None, "records": list[dict]|None, "error": str|None}`
    (`total` is the RAW probe count: GL numremaining, AP totalcount.)
- `SageClient(transport, creds)`:
  - `login() -> session_id` (raises `SageError` on error).
  - `fetch(session_id, *, build_query, count_basis, page_size) -> (records, error)`:
    probe (pagesize 1) → if error: return `([], error)` (skip object, don't abort — FR-EXT-5);
    else `total = count_basis(raw_total)`; loop `offsets(page_count(total))`; per page request: keep
    `records` only when `status_code == 200` and non-empty; stop early on an empty page. Returns all rows.
  - `fetch_gl(session_id, start, end, page_size, **qopts) -> (records, error)` (uses GL builder +
    `pagination.gl_total`).

## `sql_loader.py` — load (DB connection injected; black-box procs)
- `SqlLoader(connection, sql_cfg)` — `connection.cursor()` is DB-API-like.
- `load_gl_records(records, page_size) -> n_loaded`: chunk into page_size, `encode_page`, and for each
  call `EXEC <proc_add_gl> ?` with the JSON as a **bound parameter** (one call per chunk). `commit()`.
- Guard: `sql_cfg` targets must be `test_`-prefixed (defense-in-depth; mirrors prod_write_guard + config).
- Reconcile procs (`SyncAPLinksToGL`, `UpdateVendorInfoInGL`) deferred to W3.

## `reconcile.py` — parity metrics (pure; specs/11)
- `row_count(rows)`, `checksum(rows, key_fields)` (stable hash over key-sorted canonical rows),
  `reconcile(make_rows, util_rows, key_fields, sample) -> ReconcileResult{passed, counts, checksum_match,
  sample_diffs}`.

## `orchestrator.py` — wire the slice
- `run_gl_for_window(creds, start, end, sage_client, loader, page_size) -> {gl_records, gl_loaded, gl_error}`.

## Dry-run parity (W1 Evaluate, offline)
`tests/reconciliation/test_gl_parity_dryrun.py`: a FakeTransport emits a known GL dataset; the slice
extracts+loads into a FakeLoader; `reconcile()` compares the loaded rows to the same dataset
(stand-in for "Make prod") → must pass. A perturbed dataset → must fail. This is the offline analog of
the live parity gate; live reconciliation against Make prod is deferred to creds (OQ-11).
