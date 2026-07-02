# Questions for the Team — Intacct → SQL Automation

**What this is about:** we're rebuilding the twice-daily "Intacct feeds to SQL" job as an in-house
automation (replacing the current Make.com version). To finish it correctly and hand it over cleanly,
we need a few answers. They're grouped by who's best placed to answer. No technical background needed.

_Last updated: 2026-07-02_

---

## A. Finance / Intacct admin

**A1. ✅ ANSWERED (client):** Both happen, governed by the accounting period. If the entry's month is
still **OPEN**, it can be **deleted completely** (no record remains). If the period is **CLOSED**, it is
**reversed** in the open period (original stays; a new reversing entry is added).
⇒ Hard deletes only occur in **open (recent) periods**; closed periods only get reversals (new rows our
delta upsert already captures). The delete-sweep only needs to cover the open period(s) — validates the
windowed-sweep + accumulate design.

**A2. ✅ ANSWERED (client):** Same as GL — if the period is **open**, an AP bill can be **deleted
completely**; if **closed**, it can **only be reversed** (plus other reversal-only cases). ⇒ AP hard
deletes also only happen in open periods, so a windowed AP sweep (once the AP table has a date column,
B5) covering the open period catches all real AP deletions.

---

## B. Database (DBA) team

**B1. ✅ ANSWERED (project owner):** SQL login password expires every **3 months**; **Diana** (project
team) will own updating it going forward.

**B2. ✅ CLOSED (project owner):** leave the SQL login's rights **as-is** — no change requested.

**B3. ✅ ANSWERED (project owner):** the maintained window is based on **"Entry date"** (matches the
current project, which has worked well).

**B4. Is the SQL Server database (VPASQL01) backed up regularly, and how far back can it be restored?**
_Why: nice to know. Not critical — our tables can always be rebuilt from Intacct if needed._
> Answer:

**B5. Can you add a date column (e.g. `When_Posted`) to the AP bill table and have the AP load
procedure populate it?**
_Why: the GL table has an entry-date column, so we can safely detect and remove only bills deleted in
the recent window. The AP bill table has no date column, so until it has one we can't safely reconcile
AP deletions (removing them could wrongly delete old bills we want to keep). Everything else for AP
still works — this only affects catching AP bills that were deleted in Intacct._
> Answer:

---

## C. IT / Azure / Infrastructure team

We'd like to store the automation's passwords securely in an **Azure Key Vault**, so the team can
rotate them safely without editing files on the server. To set that up we need IT to:

**C1.** Create an **Azure Key Vault** in the same subscription/region as the automation VM
(**VTSAzPLW01**).
> Done? / Vault name:

**C2.** Turn on that VM's **system-assigned managed identity** (this lets the VM read the vault
without us storing any password anywhere).
> Done?

**C3.** Give that VM identity **read access** to the vault (role: "Key Vault Secrets User"), and give
**Diana** (she rotates the SQL password quarterly) permission to **update** secrets (role: "Key Vault
Secrets Officer").
> Done? / Confirm Diana has update rights:

**C4.** Confirm the **vault name / URL** so we can point the automation at it.
> Vault URL:

**C5 (Email).** We'd like the automation to send status and failure emails from the shared mailbox
**timeclockplus@veritashcsolutions.com**. Does that mailbox support sending via the **Microsoft Graph API**
(modern, app-based)? If yes, can IT create/allow a small **app registration** for it? If not, is basic
**SMTP sending** still enabled on that mailbox?
> Answer:

**C6 (SharePoint read).** The automation needs to **read the recipient list** (and possibly the
environment config) from `…/Integrations/Veritas Integrations/Intacct SQL Feeds/Config.xlsx`. Please
grant the VM's identity (or an app registration) **read access** to that SharePoint site via Microsoft
Graph (e.g. **Sites.Selected** scoped to this site, or Sites.Read). _Not blocking the initial build —
needed before we switch recipients from config to the live SharePoint file._
> Done? / Access method:

---

## D. From you (project owner) — inputs we need to proceed

**D1. ✅ ANSWERED:** recipients are maintained in SharePoint —
`…/Integrations/Veritas Integrations/Intacct SQL Feeds/Config.xlsx`. The automation will **read the
list from there at runtime** (team can update it without a redeploy). Two small follow-ups:
- **D1a. ✅ ANSWERED:** addresses are in `Config.xlsx` → **Sheet `Sheet1`**, **table `Table1`**. **One
  shared list** for both failure alerts and the daily report.
- **D1b.** Requires IT access (see **C6**) so the VM can read that file. As a safeguard, we'll also keep
  a **cached copy** of the last-known list + one **fallback admin address** in config, so a failure
  alert can still go out even if SharePoint is unreachable. Fallback admin address?
  > Answer:

**D2. ✅ ANSWERED:** send emails **from** `timeclockplus@veritashcsolutions.com`.

**D3.** Confirm the **run schedule** — upserts at **08:00** and **17:30**, nightly clean-up + refresh at
**01:00**, monthly deep pass on the **1st** — and **which days of the week** it should run (all 7?) and
the **timezone**.
> Answer:

**D4 (parked — no rush).** Decide whether to eventually delete very old data (older than the maintained
window) or keep all history. **Default today = keep everything.**
> Decision:

---

_Once A–D are answered we can finalise the design and begin the build (secure password store, email
notifier, and the new "only fetch what changed" delta engine for both the daily and monthly runs)._
