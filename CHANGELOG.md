# Changelog

All enhancements to the Checkflo → Sage Intacct Integration blueprints are recorded here.
Each entry corresponds to a git commit.

## [Unreleased]

### 2026-06-19
- **Make API toolkit (`tools/make_cli.py`).** Zero-dependency Python CLI to connect
  the repo to the live Make.com scenarios via the REST API: read scenarios
  (`scenarios`/`get`/`pull`), read execution logs and failures (`logs`/`log`/`dlqs`/`dlq`),
  edit scenarios with an auto-backup safety gate (`push`), and QA them
  (`qa`/`qa-diff`/`run`). Reads credentials from a `.env` (supports `MAKE_VERITAS_*`
  keys, `KEY:"value"` or `KEY=value`, parent-dir lookup) or `.make/config.json`.
  Includes a browser User-Agent to satisfy Cloudflare. Secrets and live-blueprint
  backups are gitignored. See `tools/README.md`.
- **QA gate (`make_cli.py gate`).** Comprehensive static gate beyond `qa`: reference
  integrity (`{{id.field}}` must resolve), aggregator Source-Module/feeder validity,
  invalid-slug detection, variable set/get usage, and diff-vs-baseline. Exits non-zero
  on errors and prints the non-automatable manual checklist. `collect_modules` now also
  traverses `onerror` handlers (so `qa`/`qa-diff` see error-handler modules).
- **Make integration skill (`.claude/skills/make-integration/`).** Reusable reference
  (API/toolkit, blueprint anatomy, troubleshooting, change review checklist), provenance-
  labeled and reconciled with the official `make-skills` plugin.

### 2026-06-11
- **Part 1 – "Custom fields missing" report format.** When a fetched batch's check info
  has no custom fields, module `[901]` (`Email_Body` update, Route 3 of router `[137]`)
  now reports the standard summary block instead of `Status: Custom fields missing`:
  Total checks processed in Checkflo / posted in Intacct / Variance / Amount paid from
  Checkflo / Amount of record transfer processed in intact / Variance — all `NA`.
  Applied to both schedule variants (`MoWeSa` and `TuThFr`).
- Initial import of the 7-part MAKE.com blueprint set and project documentation.
