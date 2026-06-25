#!/usr/bin/env python3
"""
PreToolUse guard for the intacct_sql_etl project.

Hard-BLOCKS any Bash/PowerShell command that WRITES to the PRODUCTION Intacct
tables or stored procedures. Dev/test work must target `test_`-prefixed objects
and run in parallel with Make (see intacct_sql_etl/CLAUDE.md + specs/11).

Contract (Claude Code hooks):
  * stdin = JSON with {tool_name, tool_input:{command, ...}, ...}
  * exit 0  -> allow
  * exit 2  -> BLOCK (stderr shown to the model/user)

Allowed:
  * Reads of prod tables (SELECT ...) — we snapshot Make's output to compare.
  * Anything targeting `test_`-prefixed tables/procs.
  * An explicit, deliberate override token `ALLOW_PROD_WRITE` in the command
    (use only with explicit user approval — effectively never in this project).
"""
import json
import re
import sys

OVERRIDE = "ALLOW_PROD_WRITE"

# Production write targets (the things we must never mutate in dev/test).
PROD_TABLES = ("GLTable", "APBillTable")
PROD_PROCS = (
    "AddGLDetailBulk",
    "AddAPBillBulk",
    "SyncAPLinksToGL",
    "UpdateVendorInfoInGL",
)

# Write verbs against a table.
WRITE_VERB = re.compile(
    r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|MERGE\b|TRUNCATE\s+TABLE|"
    r"DROP\s+TABLE|ALTER\s+TABLE|BULK\s+INSERT)\b",
    re.IGNORECASE,
)

# A prod table reference NOT prefixed by `test_` (negative lookbehind, fixed width).
PROD_TABLE_REF = re.compile(
    r"(?<!test_)\b(" + "|".join(PROD_TABLES) + r")\b", re.IGNORECASE
)
# A prod proc reference NOT prefixed by `test_`. Procs mutate by definition -> any
# (non-test_) reference in a command that also executes SQL is a write.
PROD_PROC_REF = re.compile(
    r"(?<!test_)\b(" + "|".join(PROD_PROCS) + r")\b", re.IGNORECASE
)
EXEC_VERB = re.compile(r"\b(EXEC(UTE)?|sp_executesql|Invoke-Sqlcmd)\b", re.IGNORECASE)


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except Exception:
        return 0  # not parseable -> don't interfere

    tool = data.get("tool_name", "")
    if tool not in ("Bash", "PowerShell"):
        return 0
    cmd = (data.get("tool_input") or {}).get("command", "") or ""
    if not cmd:
        return 0
    if OVERRIDE in cmd:
        return 0  # deliberate, approved opt-in

    reasons = []

    # 1) Write verb + a non-test_ prod table reference.
    if WRITE_VERB.search(cmd) and PROD_TABLE_REF.search(cmd):
        hits = sorted(set(m.group(1) for m in PROD_TABLE_REF.finditer(cmd)))
        reasons.append(f"write to production table(s): {', '.join(hits)}")

    # 2) Executing a non-test_ prod stored procedure (procs always mutate).
    if PROD_PROC_REF.search(cmd) and (EXEC_VERB.search(cmd) or WRITE_VERB.search(cmd)):
        hits = sorted(set(m.group(1) for m in PROD_PROC_REF.finditer(cmd)))
        reasons.append(f"execution of production proc(s): {', '.join(hits)}")

    if reasons:
        sys.stderr.write(
            "BLOCKED by prod_write_guard: "
            + "; ".join(reasons)
            + ".\nintacct_sql_etl dev/test must target test_-prefixed tables/procs "
            "(see intacct_sql_etl/CLAUDE.md + specs/11). "
            "Reads (SELECT) of prod are allowed. To intentionally override (requires "
            f"explicit user approval), include the token {OVERRIDE} in the command.\n"
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
