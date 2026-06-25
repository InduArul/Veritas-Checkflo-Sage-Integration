"""W1 unit tests — page encoding (specs/03 FR-TR-2). Test-first."""
import json

from intacct_etl import transform


def test_encode_page_is_compact_json():
    recs = [{"RECORDNO": "1", "AMOUNT": "10.50"}, {"RECORDNO": "2", "AMOUNT": "-3.00"}]
    out = transform.encode_page(recs)
    assert " " not in out                      # compact: no spaces between tokens
    assert json.loads(out) == recs             # round-trips


def test_encode_page_preserves_unicode():
    recs = [{"VENDORNAME": "Café Möller"}]
    out = transform.encode_page(recs)
    assert "Café Möller" in out                # ensure_ascii=False
    assert json.loads(out) == recs


def test_encode_empty():
    assert transform.encode_page([]) == "[]"


def test_encode_does_not_need_quote_escaping():
    # value with a single quote round-trips cleanly (we bind as param, not interpolate)
    recs = [{"DESCRIPTION": "O'Brien's bill"}]
    out = transform.encode_page(recs)
    assert json.loads(out)[0]["DESCRIPTION"] == "O'Brien's bill"
