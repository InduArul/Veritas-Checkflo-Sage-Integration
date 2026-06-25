"""W3/adapters — Sage transport: response parsers + xml_to_dict + transport routing (specs/02).
Pure/offline (no network). Live wire shape must be confirmed against real Sage (OQ-14). Test-first."""
from intacct_etl import sage_transport as st


# ---- xml_to_dict ----
def test_xml_to_dict_rejects_doctype():
    # CR-08: refuse DTD/entity declarations (XXE / entity-expansion hardening)
    import pytest
    malicious = '<!DOCTYPE foo [<!ENTITY x "y">]><response><x>&x;</x></response>'
    with pytest.raises(ValueError):
        st.xml_to_dict(malicious)


def test_xml_to_dict_nested_and_attributes_and_repeats():
    xml = (
        "<response><operation><result>"
        '<data listtype="gldetail" count="1" totalcount="3" numremaining="2">'
        "<GLDETAIL><RECORDNO>1</RECORDNO></GLDETAIL>"
        "<GLDETAIL><RECORDNO>2</RECORDNO></GLDETAIL>"
        "</data></result></operation></response>"
    )
    d = st.xml_to_dict(xml)
    data = d["response"]["operation"]["result"]["data"]
    assert data["@attributes"]["numremaining"] == "2"
    assert data["@attributes"]["totalcount"] == "3"
    assert isinstance(data["GLDETAIL"], list) and len(data["GLDETAIL"]) == 2


# ---- parse_login_response ----
def test_parse_login_success():
    body = {"response": {"operation": {"result": {"data": {"api": {"sessionid": "SID123"}}}}}}
    assert st.parse_login_response(body) == {"session_id": "SID123"}


def test_parse_login_error():
    body = {"response": {"operation": {"result": {
        "errormessage": {"error": {"description2": "bad creds"}}}}}}
    out = st.parse_login_response(body)
    assert "error" in out and "bad creds" in out["error"]


# ---- parse_query_response ----
def _probe_body(total=580051, num=580050):
    return {"response": {"operation": {"result": {"data": {
        "@attributes": {"count": "1", "totalcount": str(total), "numremaining": str(num)},
        "GLDETAIL": [{"RECORDNO": "1"}],
    }}}}}


def test_parse_query_probe_attributes():
    out = st.parse_query_response(_probe_body(), "GLDETAIL")
    assert out["error"] is None
    assert out["numremaining"] == 580050
    assert out["totalcount"] == 580051
    assert out["status_code"] == 200


def test_parse_query_records_list():
    body = {"response": {"operation": {"result": {"data": {
        "@attributes": {"count": "2"},
        "APBILL": [{"RECORDNO": "1"}, {"RECORDNO": "2"}],
    }}}}}
    out = st.parse_query_response(body, "APBILL")
    assert out["records"] == [{"RECORDNO": "1"}, {"RECORDNO": "2"}]


def test_parse_query_single_record_wrapped_to_list():
    body = {"response": {"operation": {"result": {"data": {"GLDETAIL": {"RECORDNO": "1"}}}}}}
    out = st.parse_query_response(body, "GLDETAIL")
    assert out["records"] == [{"RECORDNO": "1"}]


def test_parse_query_error():
    body = {"response": {"operation": {"result": {
        "errormessage": {"error": {"description2": "Invalid object"}}}}}}
    out = st.parse_query_response(body, "GLDETAIL")
    assert out["error"] == "Invalid object"
    assert out["records"] is None


# ---- transport routing (poster + body_to_dict injected) ----
def test_make_transport_routes_login_and_query():
    captured = {}

    def poster(xml):
        captured["last"] = xml
        return "<RAW/>"

    def body_to_dict(raw):
        # return different bodies based on the routed call
        if "getAPISession" in captured["last"]:
            return {"response": {"operation": {"result": {"data": {"api": {"sessionid": "S"}}}}}}
        return _probe_body()

    transport = st.make_transport(poster, body_to_dict)
    assert transport("<x><getAPISession/></x>")["session_id"] == "S"
    q = transport("<x><object>GLDETAIL</object><pagesize>1</pagesize></x>")
    assert q["numremaining"] == 580050
