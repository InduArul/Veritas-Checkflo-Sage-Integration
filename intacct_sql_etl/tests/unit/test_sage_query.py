"""W1 unit tests — Sage XML builders (specs/02). Test-first."""
from datetime import date

from intacct_etl import sage_query

GL_FIELDS = [
    "LOCATIONID", "LOCATIONNAME", "DEPARTMENTID", "DEPARTMENTTITLE", "RECORDNO",
    "ACCOUNTNO", "ACCOUNTTITLE", "ENTRY_DATE", "BATCH_DATE", "VENDORID", "VENDORNAME",
    "LINE_NO", "RECORDID", "AMOUNT", "PRDESCRIPTION", "DESCRIPTION", "SYMBOL",
]
GL_ORDER = ["AUWHENCREATED", "RECORDNO", "LINE_NO"]


def test_fmt_date():
    assert sage_query.fmt_date(date(2026, 4, 30)) == "04/30/2026"
    assert sage_query.fmt_date(date(2025, 1, 1)) == "01/01/2025"


def test_login_xml_has_getapisession_and_creds():
    creds = {"sender_id": "SND", "sender_password": "sp", "user_id": "U",
             "user_password": "up", "company_id": "CO"}
    xml = sage_query.build_login_xml(creds)
    assert "<getAPISession" in xml
    assert "<senderid>SND</senderid>" in xml
    assert "<companyid>CO</companyid>" in xml
    assert "<userid>U</userid>" in xml


def _gl_page(offset=0, page_size=2000, return_json=True):
    return sage_query.build_gl_query_xml(
        session_id="SID", start=date(2025, 1, 1), end=date(2025, 1, 31),
        offset=offset, page_size=page_size, fields=GL_FIELDS, order_by=GL_ORDER,
        return_json=return_json,
    )


def test_gl_query_object_and_fields_in_order():
    xml = _gl_page()
    assert "<object>GLDETAIL</object>" in xml
    # all fields present, in declared order
    positions = [xml.index(f"<field>{f}</field>") for f in GL_FIELDS]
    assert positions == sorted(positions)


def test_gl_query_filter_dates_and_bookid():
    xml = _gl_page()
    assert "BATCH_DATE" in xml
    assert "01/01/2025" in xml and "01/31/2025" in xml
    assert "<value>ACCRUAL</value>" in xml
    assert "BOOKID" in xml


def test_gl_query_pagesize_offset_and_returnformat():
    xml = _gl_page(offset=4000, page_size=2000, return_json=True)
    assert "<pagesize>2000</pagesize>" in xml
    assert "<offset>4000</offset>" in xml
    assert "<returnformat>json</returnformat>" in xml
    assert "<sessionid>SID</sessionid>" in xml


def test_gl_probe_has_no_returnformat_and_pagesize_1():
    xml = _gl_page(offset=0, page_size=1, return_json=False)
    assert "<pagesize>1</pagesize>" in xml
    assert "<returnformat>json</returnformat>" not in xml


def test_gl_query_custom_book_filter_and_date_field():
    xml = sage_query.build_gl_query_xml(
        session_id="SID", start=date(2025, 1, 1), end=date(2025, 1, 31),
        offset=0, page_size=1, fields=GL_FIELDS, order_by=GL_ORDER,
        return_json=False, date_field="ENTRY_DATE", book_filter="CASH",
    )
    assert "ENTRY_DATE" in xml
    assert "<value>CASH</value>" in xml


# ---- CR-07: XML escaping ----
def test_login_xml_escapes_special_chars_in_identifiers():
    creds = {"sender_id": "S&D", "sender_password": "sp", "user_id": "U<1",
             "user_password": "up", "company_id": "A&B<C"}
    xml = sage_query.build_login_xml(creds)
    assert "<companyid>A&amp;B&lt;C</companyid>" in xml
    assert "<userid>U&lt;1</userid>" in xml
    assert "<senderid>S&amp;D</senderid>" in xml
    assert "A&B<C" not in xml                      # raw unescaped form must not appear


def test_password_in_cdata_is_neutralized():
    creds = {"sender_id": "S", "sender_password": "p]]>x", "user_id": "U",
             "user_password": "up", "company_id": "C"}
    xml = sage_query.build_login_xml(creds)
    assert "]]>x]]>" not in xml                    # raw break sequence is split, not left intact


# ---- CR-06: configurable dtd_version + date_format ----
def test_dtd_version_is_configurable():
    creds = {"sender_id": "S", "sender_password": "p", "user_id": "U",
             "user_password": "up", "company_id": "C"}
    xml = sage_query.build_login_xml(creds, dtd_version="2.1")
    assert "<dtdversion>2.1</dtdversion>" in xml


def test_date_format_is_configurable():
    xml = sage_query.build_gl_query_xml(
        session_id="SID", start=date(2025, 1, 1), end=date(2025, 1, 31),
        offset=0, page_size=1, fields=GL_FIELDS, order_by=GL_ORDER,
        return_json=False, date_format="%Y-%m-%d",
    )
    assert "2025-01-01" in xml and "2025-01-31" in xml


# ---- CR-20: redaction helper for safe logging ----
def test_redact_masks_passwords():
    creds = {"sender_id": "S", "sender_password": "topsecret", "user_id": "U",
             "user_password": "alsosecret", "company_id": "C"}
    xml = sage_query.build_login_xml(creds)
    red = sage_query.redact(xml)
    assert "topsecret" not in red and "alsosecret" not in red
    assert "<![CDATA[***]]>" in red
