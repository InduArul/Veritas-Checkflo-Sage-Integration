---
name: make-integration
description: >-
  Work with Make.com (formerly Integromat) scenarios for this repo's Checkflo→Sage
  Intacct integration. Use when the user mentions Make, Make.com, a scenario,
  blueprint, "Part 1-7", Project1/Project2, "Upload CSV to Checkflo", execution
  logs, DLQ / incomplete executions, or errors like 429, 503, SC403, SC400, or
  Cloudflare 1010. Covers connecting via the Make REST API toolkit
  (tools/make_cli.py), reading/editing/QA-ing scenario blueprints, troubleshooting
  executions, and the safe edit workflow.
---

# Make.com Integration Skill

Operate Make.com scenarios from this repo: read them, read their run history,
edit blueprints, and QA changes — all through the Make REST API via the
`tools/make_cli.py` CLI built into this repo.

> **Provenance labels** used throughout the reference files:
> `[VERIFIED]` = confirmed by real API calls / real blueprint exports in this repo.
> `[MS-LEARN]` = confirmed from official Microsoft Learn docs.
> `[DOC]` = Make general knowledge; **make.com docs were not reachable** when this
> skill was written, so re-confirm `[DOC]` items against help.make.com /
> developers.make.com before relying on edge cases.

## 1. Quick start

```bash
python tools/make_cli.py doctor                 # verify auth
python tools/make_cli.py scenarios              # list scenarios + live IDs
python tools/make_cli.py pull <id> --out FILE   # download a blueprint
python tools/make_cli.py logs <id> --limit 20   # recent runs
python tools/make_cli.py dlqs <id>              # failures (incomplete executions)
python tools/make_cli.py qa FILE                # static QA of a blueprint
python tools/make_cli.py qa-diff OLD NEW        # compare two blueprints
python tools/make_cli.py gate FILE --baseline OLD   # deep QA gate (refs/aggregators/diff + checklist)
python tools/make_cli.py push <id> FILE --yes   # update live (auto-backs up first)
```

Credentials load from a `.env` (parent dir supported) or `.make/config.json`.
Keys: `MAKE_VERITAS_API_TOKEN`, `MAKE_VERITAS_ZONE`, `MAKE_VERITAS_TEAM_ID`,
`MAKE_VERITAS_ORG_ID`. See `reference/api-and-toolkit.md`.

## 2. This environment (verified) `[VERIFIED]`

| Thing | Value |
|---|---|
| Zone | `us2.make.com` |
| Organization | "Veritas Healthcare Solutions" — **org id `1903574`** |
| Team | "My Team" — **team id `33002`** |
| `/scenarios` filter | use **`teamId=33002`** (passing the *org* id → `SC403`) |

Scenario → live ID maps (Project1 7-part chain + Project2) live in `Memory.txt`.

## 3. The golden rules

1. **Never `push` blind.** Always `pull → edit → qa → qa-diff → (review) → push --yes → run → logs/dlqs`. `push` auto-backs up the live blueprint to `.make/backups/` and refuses without `--yes`.
2. **`status: 1` = success, `status: 2` = completed-with-handled-errors** `[VERIFIED]`. A run can be "OK" yet contain retried failures — check the per-module markers in the Make **UI** (the API only returns run *summary* for successful runs).
3. **The API can't show per-module error bundles for a *successful* run.** Use the UI inspector, or the DLQ (`dlqs`/`dlq`) for *incomplete* executions.
4. **Don't loop external lookups.** Static reference data (mapping tables, etc.) must be fetched **once** before a loop, not per iteration — repeated Microsoft Graph/Excel calls cause 429/503 (see `reference/troubleshooting.md`).
5. **Module IDs are identity.** When editing, preserve `id`s where possible; removing a module can break `{{id.field}}` references and webhook wiring downstream.
6. **Run `qa` and `qa-diff` on every edit** and read the diff before pushing.

## 4. Reference files

- `reference/api-and-toolkit.md` — REST API endpoints, auth, scopes, zones, the executionId format gotcha, Cloudflare UA, and every `make_cli.py` command.
- `reference/blueprint-anatomy.md` — blueprint JSON structure, module object fields, routers/iterators/aggregators, `__IMTKEY__`/`__IMTAGGLENGTH__`, SetVariables scopes, error-handler directives, mapping functions.
- `reference/troubleshooting.md` — execution status codes, DLQ, Make error codes (SC400/SC403/1010), Microsoft Graph 429-vs-503 throttling (official), and a decision tree.
- `reference/review-checklist.md` — the checklist to validate ANY proposed blueprint change before `push`.

## 5. When asked to change a scenario

1. Identify the scenario (use `scenarios` to map name→id).
2. `pull` it into `scratch/` (gitignored).
3. Read the relevant modules; trace data lineage with `{{id.field}}` references.
4. Make the edit in the pulled file.
5. `qa` (0 errors) + `qa-diff` old vs new + **`gate FILE --baseline OLD`** (must say `GATE: PASS`).
6. **Run the `reference/review-checklist.md` against the change** (the gate prints the manual items).
7. Present the diff + review to the user. Only `push --yes` after explicit approval.
8. After push: `run` (if safe) and check `logs`/`dlqs`.
