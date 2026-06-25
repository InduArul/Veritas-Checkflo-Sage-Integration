"""Sage Intacct extract orchestration (specs/02). Transport is injected.

Transport contract — `transport(xml: str) -> dict`:
  login  -> {"session_id": str}                      or {"error": str}
  query  -> {"status_code": int, "numremaining": int|None, "totalcount": int|None,
             "records": list[dict]|None, "error": str|None}
            (probe responses carry the raw Sage `_attributes`; the client picks the right basis:
             GL = numremaining + 1, AP = totalcount.)

The real HTTP/XML/JSON transport adapter is thin and integration-tested with live Sage payloads
(OQ-11); this module owns the count/pagination/aggregation/error-skip logic.

An optional `retry` wrapper (callable taking a thunk) wraps every transport call so transient
network failures are retried per config (CR-18); default is a no-op (call once).
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Optional

from . import pagination, sage_query
from .pagination import DEFAULT_PAGE_SIZE


class SageError(Exception):
    pass


def _no_retry(thunk: Callable):
    return thunk()


class SageClient:
    def __init__(self, transport: Callable[[str], dict], creds: dict,
                 *, retry: Optional[Callable] = None):
        self.transport = transport
        self.creds = creds
        self._retry = retry or _no_retry

    def _send(self, xml: str) -> dict:
        return self._retry(lambda: self.transport(xml))

    def login(self) -> str:
        dtd = self.creds.get("dtd_version", sage_query.DEFAULT_DTD_VERSION)
        resp = self._send(sage_query.build_login_xml(self.creds, dtd_version=dtd))
        if resp.get("error"):
            raise SageError(f"Sage login failed: {resp['error']}")
        sid = resp.get("session_id")
        if not sid:
            raise SageError("Sage login returned no session_id")
        return sid

    def _fetch(self, build_query, total_from_probe,
               page_size: int) -> tuple[list[dict], Optional[str]]:
        """Probe for count, then page through offsets. Returns (records, error).

        `total_from_probe(probe_dict) -> int` lets the caller pick the right count basis
        (GL numremaining+1, AP totalcount). On a Sage application error at probe time, returns
        ([], error) so the caller skips this object without aborting the run (specs/02 FR-EXT-5).
        """
        probe = self._send(build_query(offset=0, page_size=1, return_json=False))
        if probe.get("error"):
            return [], probe["error"]
        total = total_from_probe(probe)
        pages = pagination.page_count(total, page_size)
        records: list[dict] = []
        for off in pagination.offsets(pages, page_size):
            resp = self._send(build_query(offset=off, page_size=page_size, return_json=True))
            if resp.get("error"):
                return records, resp["error"]
            if resp.get("status_code") != 200:
                break
            page = resp.get("records") or []
            if not page:
                break                              # empty page guard (length > 0)
            records.extend(page)
        return records, None

    def fetch_gl(self, session_id: str, start: date, end: date,
                 page_size: int = DEFAULT_PAGE_SIZE, **qopts):
        def build(offset, page_size, return_json):
            return sage_query.build_gl_query_xml(
                session_id=session_id, start=start, end=end, offset=offset,
                page_size=page_size, return_json=return_json, creds=self.creds, **qopts,
            )

        return self._fetch(
            build, lambda p: pagination.gl_total(int(p.get("numremaining") or 0)), page_size
        )

    def fetch_ap(self, session_id: str, start: date, end: date,
                 page_size: int = DEFAULT_PAGE_SIZE, **qopts):
        def build(offset, page_size, return_json):
            # probe uses the 4-field set, page uses the 7-field set (specs/02 FR-EXT-3)
            fields = sage_query.AP_PAGE_FIELDS if return_json else sage_query.AP_PROBE_FIELDS
            return sage_query.build_ap_query_xml(
                session_id=session_id, start=start, end=end, offset=offset,
                page_size=page_size, return_json=return_json, fields=fields,
                creds=self.creds, **qopts,
            )

        return self._fetch(
            build, lambda p: pagination.ap_total(int(p.get("totalcount") or 0)), page_size
        )
