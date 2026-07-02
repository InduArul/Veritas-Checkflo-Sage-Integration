"""W-delta unit tests — keys-only sweep query builders (specs/13 §13.5). Test-first.

The delete-sweep pulls only key fields (cheap) to compare against SQL. GL is windowed on
ENTRY_DATE (B3/OQ-2) + BOOKID=ACCRUAL; AP is windowed on WHENPOSTED and selects RECORDNO only.
"""
from datetime import date

from intacct_etl import sage_query


def _select(xml):
    return xml.split("<select>", 1)[1].split("</select>", 1)[0]


def _filter(xml):
    return xml.split("<filter>", 1)[1].split("</filter>", 1)[0]


def test_gl_keys_query_selects_keys_only():
    xml = sage_query.build_gl_keys_query_xml(
        session_id="SID", start=date(2026, 3, 1), end=date(2026, 6, 30),
        offset=0, page_size=2000, return_json=True,
    )
    assert "<object>GLDETAIL</object>" in xml
    sel = _select(xml)
    assert "<field>RECORDNO</field>" in sel and "<field>LINE_NO</field>" in sel
    assert "<field>AMOUNT</field>" not in sel          # not the full field set (cheap pull)


def test_gl_keys_query_windows_entry_date_and_keeps_bookid():
    filt = _filter(sage_query.build_gl_keys_query_xml(
        session_id="SID", start=date(2026, 3, 1), end=date(2026, 6, 30),
        offset=0, page_size=1, return_json=False,
    ))
    assert "ENTRY_DATE" in filt and "03/01/2026" in filt and "06/30/2026" in filt
    assert "BOOKID" in filt and "<value>ACCRUAL</value>" in filt
    assert "<greaterthanorequalto>" in filt and "<lessthanorequalto>" in filt


def test_ap_keys_query_selects_recordno_only_windows_whenposted():
    xml = sage_query.build_ap_keys_query_xml(
        session_id="SID", start=date(2024, 1, 1), end=date(2026, 7, 2),
        offset=0, page_size=2000, return_json=True,
    )
    assert "<object>APBILL</object>" in xml
    sel = _select(xml)
    assert "<field>RECORDNO</field>" in sel
    assert "<field>VENDORNAME</field>" not in sel
    assert "WHENPOSTED" in _filter(xml)


def test_gl_keys_pagesize_offset_and_returnformat():
    xml = sage_query.build_gl_keys_query_xml(
        session_id="SID", start=date(2026, 3, 1), end=date(2026, 6, 30),
        offset=2000, page_size=2000, return_json=True,
    )
    assert "<pagesize>2000</pagesize>" in xml and "<offset>2000</offset>" in xml
    assert "<returnformat>json</returnformat>" in xml
