"""W-delta unit tests — WHENMODIFIED delta query builders (specs/13 §13.4). Test-first.

The delta upsert query filters on the change field (WHENMODIFIED) only — a strict lower bound
(> since) and an inclusive upper bound (<= until/cutoff) — and keeps the business filters
(GL: BOOKID=ACCRUAL; AP: showprivate). No posting-date window (specs/13 §13.8).
"""
from datetime import datetime

from intacct_etl import sage_query

SINCE = datetime(2026, 7, 1, 7, 0, 0)
UNTIL = datetime(2026, 7, 2, 8, 59, 0)


def _filter_block(xml: str) -> str:
    return xml.split("<filter>", 1)[1].split("</filter>", 1)[0]


def test_fmt_datetime_default_format():
    assert sage_query.fmt_datetime(datetime(2026, 7, 1, 7, 5, 9)) == "07/01/2026 07:05:09"


def test_gl_delta_filters_on_whenmodified_window():
    xml = sage_query.build_gl_delta_query_xml(
        session_id="SID", since=SINCE, until=UNTIL, offset=0, page_size=2000, return_json=True,
    )
    assert "<object>GLDETAIL</object>" in xml
    filt = _filter_block(xml)
    assert "<field>WHENMODIFIED</field>" in filt
    assert "<greaterthan>" in filt                       # strict lower bound (> since)
    assert "<lessthanorequalto>" in filt                 # inclusive upper bound (<= until)
    assert "07/01/2026 07:00:00" in filt and "07/02/2026 08:59:00" in filt


def test_gl_delta_keeps_bookid_and_drops_posting_window():
    filt = _filter_block(sage_query.build_gl_delta_query_xml(
        session_id="SID", since=SINCE, until=UNTIL, offset=0, page_size=1, return_json=False,
    ))
    assert "BOOKID" in filt and "<value>ACCRUAL</value>" in filt   # business filter kept
    assert "BATCH_DATE" not in filt                                # no posting-date window in the filter


def test_gl_delta_selects_full_load_field_set():
    xml = sage_query.build_gl_delta_query_xml(
        session_id="SID", since=SINCE, until=UNTIL, offset=0, page_size=2000, return_json=True,
    )
    for f in sage_query.GL_DEFAULT_FIELDS:               # proc mapping needs the same 17 fields
        assert f"<field>{f}</field>" in xml


def test_gl_delta_pagesize_offset_returnformat():
    xml = sage_query.build_gl_delta_query_xml(
        session_id="SID", since=SINCE, until=UNTIL, offset=4000, page_size=2000, return_json=True,
    )
    assert "<pagesize>2000</pagesize>" in xml and "<offset>4000</offset>" in xml
    assert "<returnformat>json</returnformat>" in xml


def test_ap_delta_filters_whenmodified_no_bookid_with_showprivate():
    xml = sage_query.build_ap_delta_query_xml(
        session_id="SID", since=SINCE, until=UNTIL, offset=0, page_size=2000, return_json=True,
    )
    assert "<object>APBILL</object>" in xml
    filt = _filter_block(xml)
    assert "<field>WHENMODIFIED</field>" in filt
    assert "BOOKID" not in filt                          # AP has no book filter
    assert "<showprivate>true</showprivate>" in xml


def test_delta_datetime_format_configurable():
    xml = sage_query.build_gl_delta_query_xml(
        session_id="SID", since=SINCE, until=UNTIL, offset=0, page_size=1, return_json=False,
        datetime_format="%Y-%m-%dT%H:%M:%S",
    )
    assert "2026-07-01T07:00:00" in xml
