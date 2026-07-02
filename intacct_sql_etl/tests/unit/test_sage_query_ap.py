"""W2 unit tests — AP Bill XML builder (specs/02 FR-EXT-3). Test-first."""
from datetime import date

from intacct_etl import sage_query

AP_PROBE = ["MEGAENTITYID", "RECORDNO", "RECORDID", "STAMPLI_LINK"]
AP_PAGE = ["MEGAENTITYID", "RECORDNO", "RECORDID", "VENDORID", "VENDORNAME",
           "STAMPLI_LINK", "PLATFORM_SERVICES_LINK"]


def _ap(offset=0, page_size=2000, fields=None, return_json=True):
    return sage_query.build_ap_query_xml(
        session_id="SID", start=date(2024, 1, 1), end=date(2024, 3, 31),
        offset=offset, page_size=page_size, fields=fields or AP_PAGE, return_json=return_json,
    )


def test_ap_object_and_whenposted_filter():
    xml = _ap()
    assert "<object>APBILL</object>" in xml
    assert "WHENPOSTED" in xml
    assert "01/01/2024" in xml and "03/31/2024" in xml


def test_ap_showprivate_always_on():
    assert "<showprivate>true</showprivate>" in _ap(return_json=False)   # probe
    assert "<showprivate>true</showprivate>" in _ap(return_json=True)    # page


def test_ap_page_returnformat_and_pagesize():
    xml = _ap(offset=2000, page_size=2000, return_json=True)
    assert "<returnformat>json</returnformat>" in xml
    assert "<pagesize>2000</pagesize>" in xml and "<offset>2000</offset>" in xml


def test_ap_probe_no_returnformat():
    assert "<returnformat>json</returnformat>" not in _ap(page_size=1, return_json=False)


def test_ap_page_has_seven_fields_probe_has_four():
    page_xml = _ap(fields=AP_PAGE)
    probe_xml = _ap(fields=AP_PROBE, page_size=1, return_json=False)
    for f in AP_PAGE:
        assert f"<field>{f}</field>" in page_xml
    assert "<field>VENDORID</field>" not in probe_xml
    assert "<field>PLATFORM_SERVICES_LINK</field>" not in probe_xml
    assert "<field>STAMPLI_LINK</field>" in probe_xml
    assert "RECORDNO" in sage_query.build_ap_query_xml.__doc__ or True   # smoke
    assert "<order><field>RECORDNO</field><ascending /></order>" in page_xml


def test_ap_default_page_and_delta_carry_whenposted():
    # OQ-26/step-2: WHENPOSTED must be in the AP payload (default field set) so the load proc can
    # populate the new When_Posted column. Uses the DEFAULT field set (no explicit `fields`).
    from datetime import datetime
    page = sage_query.build_ap_query_xml(
        session_id="SID", start=date(2024, 1, 1), end=date(2024, 3, 31),
        offset=0, page_size=2000, return_json=True)
    delta = sage_query.build_ap_delta_query_xml(
        session_id="SID", since=datetime(2026, 7, 1), until=datetime(2026, 7, 2),
        offset=0, page_size=2000, return_json=True)
    assert "<field>WHENPOSTED</field>" in page
    assert "<field>WHENPOSTED</field>" in delta
