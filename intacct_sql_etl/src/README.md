# src/ — Implementation

Empty until the requirement specification in `../specs/` is reviewed and signed off (spec-driven
development). Planned modules (see `../specs/00_overview.md`):

- `orchestrator.py` — run_type/window derivation, table wipe, per-env/per-chunk loop (spec 05)
- `sage_client.py` — Intacct XML gateway: login, GL/AP query, offset pagination (spec 02)
- `transform.py` — page JSON encoding, business-rule application (spec 03)
- `sql_loader.py` — bulk load via procs, reconcile, transaction/staging strategy (spec 04)
- `config.py` — load/validate config + secrets (spec 06)
- `logging_setup.py` — run log + structured events (spec 07)
- `cli.py` — entrypoint (scheduled + on-demand/backfill)
