"""Page transform/encoding (pure; specs/03 FR-TR-2).

A Sage page (list of record dicts) is serialized to compact JSON and passed to the SQL bulk
proc as a BOUND parameter — so no SQL quote-escaping is required (unlike Make's replace('->'')).
"""
from __future__ import annotations

import json


def encode_page(records: list[dict]) -> str:
    """Compact JSON for a page of records (no inter-token spaces; Unicode preserved)."""
    return json.dumps(records, separators=(",", ":"), ensure_ascii=False)

# NOTE: Make escaped single quotes (`replace("'", "''")`) because it interpolated JSON into the
# SQL text. We bind the JSON as a parameter instead, so no quote escaping is needed (see
# sql_loader._exec_proc_json). The reference helper was removed as dead code (CR-16).
