"""Sage Intacct XML query builders (pure functions; specs/02).

Reproduces the GL/AP query structure from the Make workers verbatim in shape (object, field
order, filter, orderby, options, pagesize, offset). Credentials are injected by the caller.

All interpolated values are XML-escaped (CR-07); passwords are wrapped in CDATA. The DTD version
and date format are configurable (CR-06), not hardcoded.
"""
from __future__ import annotations

from datetime import date
from xml.sax.saxutils import escape

DEFAULT_DTD_VERSION = "3.0"
DEFAULT_DATE_FORMAT = "%m/%d/%Y"

# Blank placeholder creds keep the builders pure/testable without secrets; callers pass real
# creds= when wiring. (Credentials live in the <control> block; the session id authenticates
# query calls, but the live Make scenario still repeats sender creds.)
_BLANK_CREDS = {"sender_id": "", "sender_password": ""}

GL_DEFAULT_FIELDS = [
    "LOCATIONID", "LOCATIONNAME", "DEPARTMENTID", "DEPARTMENTTITLE", "RECORDNO",
    "ACCOUNTNO", "ACCOUNTTITLE", "ENTRY_DATE", "BATCH_DATE", "VENDORID", "VENDORNAME",
    "LINE_NO", "RECORDID", "AMOUNT", "PRDESCRIPTION", "DESCRIPTION", "SYMBOL",
]
GL_DEFAULT_ORDER = ["AUWHENCREATED", "RECORDNO", "LINE_NO"]

AP_PROBE_FIELDS = ["MEGAENTITYID", "RECORDNO", "RECORDID", "STAMPLI_LINK"]
AP_PAGE_FIELDS = [
    "MEGAENTITYID", "RECORDNO", "RECORDID", "VENDORID", "VENDORNAME",
    "STAMPLI_LINK", "PLATFORM_SERVICES_LINK",
]
AP_DEFAULT_ORDER = ["RECORDNO"]


def fmt_date(d: date, date_format: str = DEFAULT_DATE_FORMAT) -> str:
    """Sage filter date format (default MM/DD/YYYY; configurable)."""
    return d.strftime(date_format)


def _x(value) -> str:
    """XML-escape an interpolated text value (& < >)."""
    return escape(str(value))


def _cdata(value) -> str:
    """Wrap a secret in CDATA, neutralizing any embedded ']]>' so it cannot break the section."""
    return "<![CDATA[" + str(value).replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _control(creds: dict, dtd_version: str = DEFAULT_DTD_VERSION) -> str:
    return (
        "<control>"
        f"<senderid>{_x(creds['sender_id'])}</senderid>"
        f"<password>{_cdata(creds['sender_password'])}</password>"
        "<controlid></controlid><uniqueid>false</uniqueid>"
        f"<dtdversion>{_x(dtd_version)}</dtdversion><includewhitespace>false</includewhitespace>"
        "</control>"
    )


def build_login_xml(creds: dict, dtd_version: str = DEFAULT_DTD_VERSION) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<request>'
        + _control(creds, dtd_version)
        + "<operation><authentication><login>"
        f"<userid>{_x(creds['user_id'])}</userid>"
        f"<companyid>{_x(creds['company_id'])}</companyid>"
        f"<password>{_cdata(creds['user_password'])}</password>"
        "<locationid></locationid>"
        "</login></authentication>"
        '<content><function controlid=""><getAPISession /></function></content>'
        "</operation></request>"
    )


def _fields_xml(fields) -> str:
    return "".join(f"<field>{_x(f)}</field>" for f in fields)


def _orderby_xml(order_by) -> str:
    orders = "".join(f"<order><field>{_x(f)}</field><ascending /></order>" for f in order_by)
    return f"<orderby>{orders}</orderby>"


def _query_xml(
    creds: dict,
    *,
    session_id: str,
    obj: str,
    fields,
    filter_xml: str,
    order_by,
    offset: int,
    page_size: int,
    options_extra: str = "",
    return_json: bool,
    dtd_version: str = DEFAULT_DTD_VERSION,
) -> str:
    opts = options_extra + ("<returnformat>json</returnformat>" if return_json else "")
    options = f"<options>{opts}</options>" if opts else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<request>'
        + _control(creds, dtd_version)
        + "<operation><authentication>"
        f"<sessionid>{_x(session_id)}</sessionid>"
        "</authentication>"
        '<content><function controlid=""><query>'
        f"<object>{_x(obj)}</object>"
        f"<select>{_fields_xml(fields)}</select>"
        f"<filter>{filter_xml}</filter>"
        f"{_orderby_xml(order_by)}"
        f"{options}"
        f"<pagesize>{int(page_size)}</pagesize>"
        f"<offset>{int(offset)}</offset>"
        "</query></function></content>"
        "</operation></request>"
    )


def build_gl_query_xml(
    *,
    session_id: str,
    start: date,
    end: date,
    offset: int,
    page_size: int,
    fields=None,
    order_by=None,
    date_field: str = "BATCH_DATE",
    book_filter: str = "ACCRUAL",
    return_json: bool,
    creds: dict | None = None,
    date_format: str = DEFAULT_DATE_FORMAT,
    dtd_version: str = DEFAULT_DTD_VERSION,
) -> str:
    fields = fields or GL_DEFAULT_FIELDS
    order_by = order_by or GL_DEFAULT_ORDER
    creds = creds or _BLANK_CREDS
    filter_xml = (
        "<and>"
        f"<greaterthanorequalto><field>{_x(date_field)}</field>"
        f"<value>{_x(fmt_date(start, date_format))}</value></greaterthanorequalto>"
        f"<lessthanorequalto><field>{_x(date_field)}</field>"
        f"<value>{_x(fmt_date(end, date_format))}</value></lessthanorequalto>"
        f"<equalto><field>BOOKID</field><value>{_x(book_filter)}</value></equalto>"
        "</and>"
    )
    return _query_xml(
        creds, session_id=session_id, obj="GLDETAIL", fields=fields,
        filter_xml=filter_xml, order_by=order_by, offset=offset,
        page_size=page_size, return_json=return_json, dtd_version=dtd_version,
    )


def build_ap_query_xml(
    *,
    session_id: str,
    start: date,
    end: date,
    offset: int,
    page_size: int,
    fields=None,
    order_by=None,
    date_field: str = "WHENPOSTED",
    show_private: bool = True,
    return_json: bool,
    creds: dict | None = None,
    date_format: str = DEFAULT_DATE_FORMAT,
    dtd_version: str = DEFAULT_DTD_VERSION,
) -> str:
    """AP Bill query (object APBILL). Filter on WHENPOSTED window; showprivate option.

    Probe uses the 4-field set, page uses the 7-field set (caller passes `fields`); RECORDNO order.
    """
    fields = fields or AP_PAGE_FIELDS
    order_by = order_by or AP_DEFAULT_ORDER
    creds = creds or _BLANK_CREDS
    filter_xml = (
        "<and>"
        f"<greaterthanorequalto><field>{_x(date_field)}</field>"
        f"<value>{_x(fmt_date(start, date_format))}</value></greaterthanorequalto>"
        f"<lessthanorequalto><field>{_x(date_field)}</field>"
        f"<value>{_x(fmt_date(end, date_format))}</value></lessthanorequalto>"
        "</and>"
    )
    options_extra = "<showprivate>true</showprivate>" if show_private else ""
    return _query_xml(
        creds, session_id=session_id, obj="APBILL", fields=fields,
        filter_xml=filter_xml, order_by=order_by, offset=offset,
        page_size=page_size, options_extra=options_extra, return_json=return_json,
        dtd_version=dtd_version,
    )


def redact(xml: str) -> str:
    """Mask CDATA-wrapped secrets so a request can be logged safely (CR-20).

    NEVER log raw request XML — it contains credentials. Use this helper if a request must be
    logged for diagnostics.
    """
    import re

    return re.sub(r"<!\[CDATA\[.*?\]\]>", "<![CDATA[***]]>", xml, flags=re.DOTALL)
