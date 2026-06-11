# Checkflo → Sage Intacct Integration

MAKE.com automation that pulls check/payment batches from **Checkflo**, validates them,
looks up vendors and bills in **Sage Intacct**, builds AP Payment XML, posts & approves
the payments in Intacct, and emails a summary report.

The integration is split across **7 MAKE scenarios** ("Parts") that are chained together
by webhooks. Each scenario finishes by firing the next scenario's webhook.

> **Repository note:** This repo holds the exported MAKE **Blueprint JSON** files
> (under `Source code/`). To deploy a change: edit the blueprint, then re-import it into
> the corresponding MAKE scenario (Scenario → ··· menu → *Import Blueprint*).

---

## Scenario chain

```
        ┌─────────── SCHEDULED ───────────┐
        │  Part 1  (two schedule copies)   │
        │  Fetch Checkflo details          │
        │  • MoWeSa  • TuThFr              │
        └───────────────┬──────────────────┘
                        │  webhook → Part 2
                        ▼
        ┌──────────────────────────────────┐
        │  Part 2  Main processing          │
        │  • Sage session ID                │
        │  • loops each check in the batch  │
        └──┬────┬────┬─────────┬─────────┬──┘
           │    │    │         │         │
   webhook │    │    │         │         │ webhook
           ▼    ▼    ▼         ▼         ▼
        Part 3 Part 4 Part 5  Part 6   Part 7
        Vendor Neg.   Loop    Intacct  Reporting
        Details Checks Invoices Posting (email)
```

### Webhook wiring

| Caller | Module | Calls | Target webhook URL token |
|--------|--------|-------|--------------------------|
| Part 1 | `[920]` | **Part 2** | `66wfl8mu…` |
| Part 2 | `[965]` Call to fetch Vendor details | **Part 3** | `qwqt56ur…` |
| Part 2 | `[923]` Call Negative Checks processing | **Part 4** | `0az986uo…` |
| Part 2 | `[929]` Call for Looping All Invoices | **Part 5** | `1ocdok2p…` |
| Part 2 | `[962]` Call for Payment posting | **Part 6** | `sotf1rt4…` |
| Part 2 | `[930]` Call for Reporting | **Part 7** | `y67yddn4…` |

### Trigger webhook hook IDs

| Part | Scenario | Trigger hook ID | Hook label |
|------|----------|-----------------|------------|
| 2 | Main processing | `2336197` | Sage_Integration_Part2 |
| 3 | Fetch Vendor Details | `2369900` | Sage_Integration_Fetch Vendor Details_Part3 |
| 4 | Negative Checks | `2346701` | Sage_Intaact_Negativechecks_processing |
| 5 | Looping All Invoices | `2346849` | Intacct Integration_Looping ALL checks |
| 6 | Intacct Posting | `2355047` | Sage_Integration_Posting_Part5 |
| 7 | Reporting | `2355018` | Sage_Integration_Part2 *(label is a misnomer)* |

---

## The scenarios

### Part 1 — Fetch Checkflo details  *(scheduled)*
Files: `…_MoWeSa.blueprint.json` and `…_TuThFr.blueprint.json`

The entry point. Two **structurally identical** copies exist, differing only by schedule
(Mon/Wed/Sat vs Tue/Thu/Fri).

> ⚠️ **Any logic change to Part 1 must be applied to BOTH files.**

Flow:
1. Read configuration from a SharePoint Excel workbook (worksheet + tables).
2. Pull **SAGE** (`1862`) and **Checkflo** (`1859`) credentials from datastores.
3. List all batches to process.
4. Router `[635]`:
   - **Route 1** – first run: create/upload a fresh Log file to SharePoint, add Log table.
   - **Route 2** – nothing to process path.
   - **Route 3** – fetch batch record from Checkflo (`[89]`), then router `[137]` validates
     the batch (bank mapping `[250]`, error tracking) and on success fires **Part 2** (`[920]`).

### Part 2 — Main processing  *(webhook)*
The orchestrator. For the incoming batch:
1. `[155]` HTTP request → SAGE **session ID**.
2. Initialises trackers: `value_of_payment_posted_for_a_batch`, `Total_value_of_checks_from_checkflo`.
3. `[104]` **Loop each check in the batch**, and per check calls in sequence:
   - **Part 3** (vendor details) → **Part 4** (negative checks) → **Part 5** (loop invoices) → **Part 6** (payment posting).
4. After posting, computes **variances** (`variance_no_of_checks` `[429]`, `variance_amount` `[430]`)
   and on completion fires **Part 7** (reporting) `[930]`.

### Part 3 — Fetch Vendor Details  *(webhook)*
Resolves the Sage **Vendor ID** for a check, with cascading fallback lookups
(`[532]`→`[282]`→`[813]`). Converts Checkflo custom fields into arrays. Responds to the
caller via `gateway:WebhookRespond`.

### Part 4 — Negative Checks  *(webhook)*
Loops the negative invoices on a check, fetches each bill from Sage, and stores qualifying
negative bills in the **`Negative_Bill`** datastore (`4264`) for later offsetting.

### Part 5 — Looping All Invoices  *(webhook)*
Iterates every invoice on a check, fetches bill details from Sage, and builds the
**`APPayment` XML** (`xml:TransformToXML`), applying negative-bill offsets pulled from the
`Negative_Bill` datastore. Returns the assembled payment payload.

### Part 6 — Intacct Posting  *(webhook)*
1. Builds the AP Payment XML and inserts payment details.
2. `[272]` Create AP Payment request to Sage → `[396]` Query the request → `[171]` Approve
   the AP Payment.
3. On success, updates `value_of_payment_posted_for_a_batch`. Errors are written to the Log file.

### Part 7 — Reporting  *(webhook)*
Builds the email body from the `Email_Body` datastore (`3445`), reads the recipient list
from a SharePoint Excel sheet, and sends the summary email (`microsoft-email:createAndSendAMessage`).

---

## Shared infrastructure

### Datastores
| ID | Name | Purpose |
|----|------|---------|
| `1859` | `checkflo_creds` | Checkflo API credentials |
| `1862` | `Sage_creds` | Sage Intacct credentials |
| `3103` | `temp_var` | Cross-module state / indicators within a run |
| `3445` | `Email_Body` | Accumulated email/report text |
| `4264` | `Negative_Bill` | Negative bills staged for offsetting against payments |

### External systems
- **Checkflo API** – source of check/payment batches.
- **Sage Intacct API** – session auth, vendor lookup, bill lookup, AP Payment create/query/approve.
- **Microsoft SharePoint / Excel** – configuration, Log file, Batch Tracker, recipient list.
- **Microsoft 365 email** – summary report delivery.

### Conventions
- State is passed between modules largely via `util:SetVariables` / `util:GetVariables`.
- Most steps write progress/errors to a shared SharePoint **Log file** and **Batch Tracker** Excel.
- Inter-scenario calls use `http:ActionSendData` → the next scenario's `gateway:CustomWebHook`,
  with the callee replying via `gateway:WebhookRespond`.

---

## Working on this project

- Blueprints are referenced by **module ID** (the `[NNN]` numbers above and in `id` fields).
- All changes are tracked in git; each enhancement is committed separately with a description.
- See [`CHANGELOG.md`](CHANGELOG.md) for the running history of enhancements.
