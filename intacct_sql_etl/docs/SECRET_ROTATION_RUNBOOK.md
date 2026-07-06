# Secret Rotation Runbook (OQ-11 / OQ-22 / gate #11)

How secrets are stored and rotated for the Intacct SQL ETL. **No secret ever lives in source, committed
config, or logs** (CLAUDE.md #7). The app reads secrets through a swappable `SecretProvider`.

## Secret store (today vs future)
- **Today:** `EnvSecretProvider` — process environment, loaded from **`/data/intacct-etl/intacct-etl.env`**
  (`chmod 600`, git-ignored). `run-etl.sh` sources it; the CLI's provider resolves each key by name.
- **Future (OQ-22, blocked on IT):** Azure Key Vault + VM Managed Identity. **Swap is config-only, no
  code change** — repoint the provider to Key Vault. When IT is ready: create the vault, enable the VM
  managed identity ("Key Vault Secrets User"), grant **Diana** "Key Vault Secrets Officer", store the
  secrets below, then flip the provider.

## Secrets under management (key names in `.env`)
| Secret | .env key(s) | Owner / cadence |
|---|---|---|
| SQL login password | `SQL_PASSWORD` | **Diana — quarterly** (the SQL login expires every ~3 months) |
| Sage creds (per env) | `Prod_Sage_<Env>_SENDER_ID/_SENDER_PASSWORD/_USER_ID/_USER_PASSWORD/_COMPANY_ID` | as-needed (Sage admin) |
| SMTP / alert mailbox | `SMTP_USERNAME` / `SMTP_PASSWORD` (once #8 unblocked) | IT — per mailbox policy |

## Procedure — rotate the SQL password (quarterly)
1. **Rotate at the source:** DBA/Diana sets the new password on the SQL login (`indu`) in SQL Server.
2. **Update the secret store (no code change):**
   - *Env mode (today):* edit `/data/intacct-etl/intacct-etl.env`, set `SQL_PASSWORD=<new>`; keep the
     file `chmod 600`. Do **not** commit it.
   - *Key Vault mode (future):* update the `sql-password` secret version in the vault.
3. **Verify before the next scheduled run:** run a read-only dry-run —
   `run-etl.sh --config /data/intacct-etl/config.yaml --mode upsert` off-hours (small delta) and
   confirm `DONE ETL rc=0` in the run log. A bad password fails pre-flight loudly (OQ-30).
4. **Rollback:** if the run fails on auth, restore the previous `SQL_PASSWORD` value and re-run; the
   watermark did **not** advance on failure (proven, gate #10 drill C), so no data is skipped.
5. **Record:** note the rotation date; next due in ~3 months.

## Procedure — rotate Sage creds (as-needed, per env)
Same pattern: update the `Prod_Sage_<Env>_*` keys in the store, dry-run that env, confirm rc=0,
rollback by restoring prior values on failure. Per-env isolation means one env's bad creds fail only
that env (failure isolation), not the whole run.

## Guardrails that make rotation safe
- **No code change** to rotate — values are resolved by name from the provider.
- **Fail-loud pre-flight** on bad SQL auth (OQ-30) — a wrong password aborts + alerts rather than
  running half-broken.
- **Watermark integrity on failure** (gate #10) — a failed run never advances the watermark, so a
  botched rotation loses no data; fix the secret and re-run.
- **Secrets never logged** — `sage_query.redact()` scrubs request XML; `.env` is `chmod 600`.

## Open dependency
KeyVault provisioning + managed identity is **blocked on IT** (OQ-22). Until then, env/`.env` is the
store and this runbook uses it. The migration to Key Vault is a config flip with zero code change.
