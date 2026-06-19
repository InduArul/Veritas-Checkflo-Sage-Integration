# Make API toolkit (`make_cli.py`)

Connects this repo to the live **Make.com** scenarios via the Make REST API.
Zero dependencies — pure Python 3 stdlib. No `pip install`, no `jq` needed.

## 1. One-time setup

1. In Make: **Profile → API / MCP access → Add token**. Grant read scopes
   (`scenarios:read`, `dlqs:read`, `scenario-logs:read`) plus `scenarios:write`
   if you want `push` to update live scenarios.
2. Find your **zone** in your Make URL (e.g. `eu2.make.com` → zone `eu2`).
3. Find your **Team ID** (team URL/settings) or **Organization ID**.
4. Provide them either way:

   **Env vars (recommended — nothing on disk):**
   ```bash
   export MAKE_API_TOKEN="xxxxxxxx"
   export MAKE_ZONE="eu2"
   export MAKE_TEAM_ID="123456"
   ```
   **or** copy `.make/config.example.json` → `.make/config.json` and fill it in
   (that file is gitignored).

5. Verify:
   ```bash
   python tools/make_cli.py doctor
   ```

## 2. Commands

| Goal | Command |
|------|---------|
| List all scenarios (find the IDs) | `python tools/make_cli.py scenarios` |
| Scenario metadata | `python tools/make_cli.py get <id>` |
| **Read** a scenario → file | `python tools/make_cli.py pull <id> --out "Source code/PartX.blueprint.json"` |
| **Read run logs** | `python tools/make_cli.py logs <id> --limit 20` |
| One execution's detail | `python tools/make_cli.py log <id> <executionId>` |
| **Failures w/ bundle data** | `python tools/make_cli.py dlqs <id>` then `dlq <dlqId>` |
| **QA** a blueprint (offline) | `python tools/make_cli.py qa "Source code/PartX.blueprint.json"` |
| **QA** compare two versions | `python tools/make_cli.py qa-diff old.json new.json` |
| Live test run | `python tools/make_cli.py run <id>` |
| **Edit/modify** live scenario | `python tools/make_cli.py push <id> "Source code/PartX.blueprint.json" --yes` |

## 3. Safe edit workflow

```
pull <id> --out file   →   edit the JSON   →   qa file   →   qa-diff (old vs new)
        →   push <id> file --yes   →   run <id>   →   logs/dlqs <id>
```

`push` always **backs up the live blueprint** to `.make/backups/<id>-<timestamp>.json`
before changing anything, and refuses to apply unless you pass `--yes`. If local QA
finds structural errors it aborts (override with `--force`). Roll back by pushing the
backup file.
