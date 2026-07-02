# 14 — Secret Provider & Email Notifications

**Status:** DRAFT for sign-off (design agreed with user 2026-07-02). No code until approved.
**Principle (user decision):** *build now, repoint later.* Every external integration (secret store,
recipient list, email transport) sits behind a small swappable interface with a "works today"
implementation and a "repoint later" implementation — so the build never blocks on IT provisioning
Key Vault, the mailbox method, or SharePoint access.

Related: `specs/06` (config/secrets), `specs/07` (logging/error handling), `specs/12` (audit trail),
`specs/13` (delta engine — the source of the counts the daily report renders). Team asks live in
`docs/TEAM_QUESTIONS.md` (C1–C6, D-series).

---

## Part A — Secret Provider

### 14.1 Goal
Resolve secrets (SQL password, Sage creds, email creds) at runtime **into memory** — never written
to disk, never in committed config/logs (`specs/06` §7). A swappable provider lets us start with the
existing `.env` and later repoint to **Azure Key Vault** with **no code change**.

### 14.2 Interface
```
class SecretProvider:
    def get(self, name: str) -> str: ...        # raise ConfigError if absent/empty
```
- `resolve_secret()` / `resolve_env_creds()` (today read `os.environ`) are refactored to take a
  `SecretProvider` (default = `EnvSecretProvider`). Behaviour is unchanged for the env case, so all
  existing tests keep passing.

### 14.3 Implementations
| Provider | Use | How it authenticates |
|---|---|---|
| `EnvSecretProvider` (default now) | `.env` / process env (chmod 600, as today) | n/a — reads the environment |
| `KeyVaultSecretProvider` (later) | Azure Key Vault | **VM system-assigned managed identity** → *no bootstrap secret* |

- Config selects it: `secrets.provider: env | keyvault`; `secrets.keyvault.vault_url: <url>`.
- Key Vault secret **names** map from logical keys via config (e.g. `SQL_PASSWORD → sql-password`),
  so switching providers is config-only.
- **Rotation (self-service, user goal):** the team updates one Key Vault secret (portal or
  `az keyvault secret set`); the next run picks it up. **Diana** = "Key Vault Secrets Officer"; the VM
  identity = "Key Vault Secrets User" (read). (OQ-22 / B1.)

### 14.4 Pre-flight + expiry handling (SQL password expires ~quarterly, B1)
- A **read-only pre-flight** at run start: resolve SQL secret → open a connection → `SELECT 1`.
- On **auth failure**, do **not** run a silent broken job: emit a **specific** alert (Part B) —
  *"SQL login failed — the password may have expired/rotated. Update the secret `sql-password`
  (Key Vault) / `.env`."* — and exit non-zero.
- Optional (later): if the store exposes secret expiry metadata, warn N days before expiry.

### 14.5 Non-goals / notes
- We do **not** rewrite `.env` at runtime (the user's first instinct) — fetch-to-memory avoids
  partial-write / permission / concurrency failure modes and never re-persists the secret.
- TLS to SQL stays `Encrypt=yes;TrustServerCertificate=yes` (risk-accepted, internal LAN, no PHI — #2).

---

## Part B — Email Notifications

### 14.6 Goal
Two events, one notifier module, best-effort (never crashes or blocks the run — `specs/07`):
1. **Failure alert** — on a degraded/failed run (or pre-flight failure): what failed, where, and the
   remediation hint.
2. **Daily success report** — on a clean run: a readable summary of the run.

Send **from** the shared mailbox **`timeclockplus@veritashcsolutions.com`** (D2); credentials in the
secret store (Part A).

### 14.7 Transport (swappable)
| Transport | Use | Notes |
|---|---|---|
| injected/mock | tests | no network |
| **Graph `sendMail`** (preferred) | prod | app registration or managed identity; survives M365 basic-auth retirement |
| SMTP AUTH | fallback | only if the mailbox still has basic SMTP enabled |
- `notify.transport: graph | smtp`. Confirm which the mailbox supports → **C5**.

### 14.8 Recipients (swappable source — build now, repoint later)
- `RecipientSource.list() -> list[str]`.
- **Now:** `ConfigRecipientSource` (a list in `config.yaml`).
- **Later:** `SharePointRecipientSource` — reads `Config.xlsx` → **sheet `Sheet1`, table `Table1`**
  (D1a) via Microsoft Graph workbook API, once IT grants read access (**C6**).
- **One shared list** for both events (D1a).
- **Safeguard (important):** cache the last-known-good list to a local file **and** keep a
  `notify.fallback_admin` address in config. If the SharePoint read fails, use cache → fallback, so a
  **failure alert can never be blocked by a SharePoint outage**. Log when the fallback is used.

### 14.9 Content
- **Daily report** (rendered from the `RunAudit` JSON, `specs/12` — no secrets, FR-AUD-8): mode,
  run_type, window, per-environment **read / upserted / inserted-vs-updated¹ / deleted**, timings,
  **watermark advanced to**, target-table totals, overall status. Subject e.g.
  `[Intacct→SQL] ✅ daily OK — Evergreen: 42 upserted, 3 swept`.
- **Failure alert:** failing env/object/step, error message, remediation hint, link/path to the audit
  JSON. Subject e.g. `[Intacct→SQL] ❌ FAILED — Evergreen GL: <error>`.
- ¹ insert-vs-update split needs the bulk proc to OUTPUT counts (OQ-15); until then report
  upserted + deleted (available from `MERGE`-less `rows_affected` = 0 caveat noted in `specs/13`).

### 14.10 Wiring
- Runs **after** the orchestrator returns (CLI, `specs/05`): choose event from `result.ok`, build the
  message from `result.audit`, resolve recipients, send via the transport. Failures to *send* are
  logged and never change the run's exit code (data outcome ≠ notification outcome).
- Pre-flight failure (14.4) triggers the failure alert before the main run is attempted.

### 14.11 Config (additions to `config.example.yaml`)
```
secrets:
  provider: env                      # env | keyvault
  keyvault:
    vault_url: ""                    # https://<vault>.vault.azure.net/  (when provider: keyvault)
    name_map: { SQL_PASSWORD: sql-password }   # logical key -> KV secret name

notify:
  enabled: true
  transport: graph                   # graph | smtp
  from_address: timeclockplus@veritashcsolutions.com
  events: { on_failure: true, on_success: true }
  recipient_source: config           # config | sharepoint
  recipients: []                     # used when recipient_source: config
  fallback_admin: ""                 # always-reachable safety recipient
  cache_path: ./cache/recipients.json
  sharepoint:                        # used when recipient_source: sharepoint (C6)
    file: ".../Intacct SQL Feeds/Config.xlsx"
    sheet: Sheet1
    table: Table1
```

### 14.12 Test plan (TDD)
- SecretProvider: env impl resolves/raises; provider selection from config; creds refactor keeps
  existing behaviour (regression). KeyVault impl deferred (interface + a faked client test only).
- Pre-flight: success path; auth-fail path → alert built + non-zero exit (injected connect).
- Notifier: builds correct subject/body from a sample `RunAudit` (success + failure); recipient
  source config vs cached-fallback when SharePoint read raises; transport injected (asserts payload,
  no network); a send exception does not change the run exit code.

---

## 14.13 Open questions (new)
- **OQ-27 [IT] Graph vs SMTP for the shared mailbox** (C5) — determines `notify.transport` + whether an
  app registration is needed.
- **OQ-28 [IT] SharePoint read access** (C6) — managed identity / app registration scope to read
  `Config.xlsx`; until granted, `recipient_source: config` is used.
- **OQ-29 [user] Report cadence/branding** — is one daily success email enough (vs per-run)? Any
  subject-line convention or logo the finance team expects? (Default: one email per run, plain HTML.)
- **OQ-30 [user/DBA] Pre-flight strictness** — on SQL auth failure, abort the whole run (default) or
  attempt and let it fail per environment? (Default: abort + alert — fail fast, loud.)
