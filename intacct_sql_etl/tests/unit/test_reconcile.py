"""W1 unit tests — reconciliation metrics (specs/11). Test-first."""
from intacct_etl import reconcile

KEYS = ["RECORDNO", "LINE_NO"]
A = [
    {"RECORDNO": "1", "LINE_NO": "1", "AMOUNT": "10.00"},
    {"RECORDNO": "1", "LINE_NO": "2", "AMOUNT": "5.00"},
    {"RECORDNO": "2", "LINE_NO": "1", "AMOUNT": "7.50"},
]


def test_row_count():
    assert reconcile.row_count(A) == 3


def test_checksum_order_independent():
    shuffled = list(reversed(A))
    assert reconcile.checksum(A, KEYS) == reconcile.checksum(shuffled, KEYS)


def test_checksum_changes_on_value_change():
    b = [dict(r) for r in A]
    b[0]["AMOUNT"] = "999.00"
    assert reconcile.checksum(A, KEYS) != reconcile.checksum(b, KEYS)


def test_reconcile_pass_identical():
    res = reconcile.reconcile(A, list(reversed(A)), key_fields=KEYS)
    assert res.passed
    assert res.row_count_make == res.row_count_util == 3
    assert res.checksum_match
    assert res.sample_diffs == []


def test_reconcile_fail_on_count_mismatch():
    res = reconcile.reconcile(A, A[:2], key_fields=KEYS)
    assert not res.passed
    assert res.row_count_make == 3 and res.row_count_util == 2


def test_reconcile_fail_on_value_mismatch_reports_sample():
    b = [dict(r) for r in A]
    b[2]["AMOUNT"] = "0.00"
    res = reconcile.reconcile(A, b, key_fields=KEYS)
    assert not res.passed
    assert not res.checksum_match
    assert any(d.get("key") == ("2", "1") for d in res.sample_diffs)
