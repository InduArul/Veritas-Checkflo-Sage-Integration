"""Reconciliation metrics for parity testing (pure; specs/11).

Compares two row sets (utility vs Make prod) by row count + order-independent checksum +
sample full-row diffs. Used by the dry-run parity gate and (later) the live parity gate.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass
class ReconcileResult:
    passed: bool
    row_count_make: int
    row_count_util: int
    checksum_match: bool
    sample_diffs: list = field(default_factory=list)


def row_count(rows) -> int:
    return len(rows)


def _key_of(row, key_fields):
    return tuple(str(row.get(k, "")) for k in key_fields)


def _canonical(row) -> str:
    # stable, type-insensitive serialization (all values as strings, keys sorted)
    return json.dumps({k: str(v) for k, v in sorted(row.items())}, ensure_ascii=False)


def checksum(rows, key_fields) -> str:
    h = hashlib.sha256()
    for row in sorted(rows, key=lambda r: _key_of(r, key_fields)):
        h.update(_canonical(row).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def reconcile(make_rows, util_rows, key_fields, sample: int = 5) -> ReconcileResult:
    checksum_make = checksum(make_rows, key_fields)
    checksum_util = checksum(util_rows, key_fields)
    checksum_match = checksum_make == checksum_util

    diffs = []
    if not checksum_match:
        make_by = {_key_of(r, key_fields): r for r in make_rows}
        util_by = {_key_of(r, key_fields): r for r in util_rows}
        for key in sorted(set(make_by) | set(util_by)):
            make_row, util_row = make_by.get(key), util_by.get(key)
            if make_row != util_row:
                diffs.append({"key": key, "make": make_row, "util": util_row})
            if len(diffs) >= sample:
                break

    passed = (
        len(make_rows) == len(util_rows)
        and checksum_match
    )
    return ReconcileResult(
        passed=passed,
        row_count_make=len(make_rows),
        row_count_util=len(util_rows),
        checksum_match=checksum_match,
        sample_diffs=diffs,
    )
