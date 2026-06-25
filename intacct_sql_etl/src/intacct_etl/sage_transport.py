"""Sage Intacct HTTP transport + response parsers (specs/02).

Pure parsers operate on a Python dict (the parsed response body). `make_transport` wires a
`poster` (xml -> raw body) and `body_to_dict` (raw -> dict) into the normalized transport contract
used by SageClient. `make_http_transport` is the real adapter (requests + xml_to_dict), imported
lazily so unit tests need no network.

NOTE (OQ-14): the exact live wire shape (XML envelope vs returnformat=json payload, attribute
key names) must be confirmed against a real Sage response. Parsers are tolerant and documented.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET


# ---------- xml -> dict ----------
def _elem_to_obj(el):
    children = list(el)
    if not children:
        text = (el.text or "").strip()
        if el.attrib:
            out = {"@attributes": dict(el.attrib)}
            if text:
                out["#text"] = text
            return out
        return text
    out = {}
    if el.attrib:
        out["@attributes"] = dict(el.attrib)
    for child in children:
        obj = _elem_to_obj(child)
        if child.tag in out:
            if not isinstance(out[child.tag], list):
                out[child.tag] = [out[child.tag]]
            out[child.tag].append(obj)
        else:
            out[child.tag] = obj
    return out


def xml_to_dict(s: str) -> dict:
    # CR-08: reject DTDs/external entities before parsing (defense against XXE / entity
    # expansion). stdlib ElementTree does not resolve external entities, but a DOCTYPE is never
    # expected in a Sage response, so we refuse it outright rather than trust the parser.
    if "<!DOCTYPE" in s or "<!ENTITY" in s:
        raise ValueError("XML with DOCTYPE/ENTITY declarations is not allowed")
    root = ET.fromstring(s)
    return {root.tag: _elem_to_obj(root)}


# ---------- navigation helpers (tolerant of single-vs-list nesting) ----------
def _first(x):
    return x[0] if isinstance(x, list) else x


def _get(d, *path):
    cur = d
    for key in path:
        cur = _first(cur)
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return _first(cur)


def _error_message(body) -> str | None:
    em = _get(body, "response", "operation", "result", "errormessage")
    if not em:
        return None
    desc = _get({"x": em}, "x", "error", "description2") or _get({"x": em}, "x", "error", "description")
    return desc or "Sage error"


# ---------- parsers ----------
def parse_login_response(body: dict) -> dict:
    err = _error_message(body)
    if err:
        return {"error": err}
    sid = _get(body, "response", "operation", "result", "data", "api", "sessionid")
    if not sid:
        return {"error": "no sessionid in login response"}
    return {"session_id": sid}


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_query_response(body: dict, object_name: str) -> dict:
    err = _error_message(body)
    if err:
        return {"status_code": 200, "numremaining": None, "totalcount": None,
                "records": None, "error": err}
    data = _get(body, "response", "operation", "result", "data")
    attrs = (data or {}).get("@attributes", {}) if isinstance(data, dict) else {}
    records = None
    if isinstance(data, dict):
        raw = data.get(object_name)
        if raw is None and "#text" in data:        # returnformat=json payload as text
            try:
                raw = json.loads(data["#text"])
            except (ValueError, TypeError):
                raw = None
        if isinstance(raw, dict):
            records = [raw]
        elif isinstance(raw, list):
            records = raw
        elif isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                records = parsed if isinstance(parsed, list) else [parsed]
            except (ValueError, TypeError):
                records = None
    return {
        "status_code": 200,
        "numremaining": _as_int(attrs.get("numremaining")),
        "totalcount": _as_int(attrs.get("totalcount")),
        "records": records,
        "error": None,
    }


# ---------- transport wiring ----------
def _object_name(xml: str) -> str:
    marker = "<object>"
    if marker in xml:
        return xml.split(marker, 1)[1].split("</object>", 1)[0].strip()
    return ""


def make_transport(poster, body_to_dict):
    """Build the normalized transport(xml)->dict from a poster + body parser (both injectable)."""

    def transport(xml: str) -> dict:
        body = body_to_dict(poster(xml))
        if "getAPISession" in xml:
            return parse_login_response(body)
        return parse_query_response(body, _object_name(xml))

    return transport


def make_http_transport(endpoint: str, *, timeout: int = 60, gzip: bool = True,
                        verify: bool = True):
    """Real adapter: POST XML to `endpoint` and parse the XML body. `requests` imported lazily."""
    import requests  # noqa: WPS433 (lazy: keeps unit tests driver/network free)

    headers = {"Content-Type": "application/xml"}
    if gzip:
        headers["Accept-Encoding"] = "gzip"

    def poster(xml: str) -> str:
        resp = requests.post(endpoint, data=xml.encode("utf-8"), headers=headers,
                             timeout=timeout, verify=verify)
        resp.raise_for_status()
        return resp.text

    return make_transport(poster, xml_to_dict)
