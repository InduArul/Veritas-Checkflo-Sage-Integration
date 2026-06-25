"""W1 unit tests — SageClient extract orchestration with a fake transport (specs/02). Test-first."""
from datetime import date

import pytest

from intacct_etl import sage_client

CREDS = {"sender_id": "S", "sender_password": "p", "user_id": "U",
         "user_password": "p", "company_id": "C"}


class FakeTransport:
    """Emulates the normalized Sage transport for GL: login + probe + paged pages."""

    def __init__(self, total_records, raw_total, page_size=2000, probe_error=None):
        self.total_records = total_records
        self.raw_total = raw_total          # what the probe reports (GL: numremaining)
        self.page_size = page_size
        self.probe_error = probe_error
        self.dataset = [{"RECORDNO": str(i), "AMOUNT": "1.00"} for i in range(total_records)]
        self.page_requests = []

    def __call__(self, xml):
        if "getAPISession" in xml:
            return {"session_id": "SID"}
        if "<pagesize>1</pagesize>" in xml:                 # probe
            if self.probe_error:
                return {"status_code": 200, "numremaining": None, "records": None,
                        "error": self.probe_error}
            # GL probe reports numremaining (client adds 1)
            return {"status_code": 200, "numremaining": self.raw_total, "records": None, "error": None}
        # page fetch — extract offset
        off = int(xml.split("<offset>")[1].split("</offset>")[0])
        self.page_requests.append(off)
        chunk = self.dataset[off:off + self.page_size]
        return {"status_code": 200, "total": None, "records": chunk, "error": None}


def test_login_returns_session():
    c = sage_client.SageClient(FakeTransport(0, -1), CREDS)
    assert c.login() == "SID"


def test_login_error_raises():
    def t(xml):
        return {"error": "bad creds"}
    c = sage_client.SageClient(t, CREDS)
    with pytest.raises(sage_client.SageError):
        c.login()


def test_fetch_gl_single_page():
    # 1500 records, numremaining=1499 -> gl_total=1500 -> 1 page
    tr = FakeTransport(total_records=1500, raw_total=1499)
    c = sage_client.SageClient(tr, CREDS)
    recs, err = c.fetch_gl("SID", date(2025, 1, 1), date(2025, 1, 31), page_size=2000)
    assert err is None
    assert len(recs) == 1500
    assert tr.page_requests == [0]


def test_fetch_gl_multi_page_aggregates_all():
    # 5000 records, numremaining=4999 -> gl_total=5000 -> 3 pages (2000,2000,1000)
    tr = FakeTransport(total_records=5000, raw_total=4999)
    c = sage_client.SageClient(tr, CREDS)
    recs, err = c.fetch_gl("SID", date(2025, 1, 1), date(2025, 1, 31), page_size=2000)
    assert err is None
    assert len(recs) == 5000
    assert tr.page_requests == [0, 2000, 4000]
    assert [r["RECORDNO"] for r in recs] == [str(i) for i in range(5000)]


def test_fetch_gl_probe_error_skips_object():
    tr = FakeTransport(total_records=0, raw_total=0, probe_error="Invalid object")
    c = sage_client.SageClient(tr, CREDS)
    recs, err = c.fetch_gl("SID", date(2025, 1, 1), date(2025, 1, 31), page_size=2000)
    assert recs == []
    assert err == "Invalid object"
    assert tr.page_requests == []           # no pages fetched on probe error


def test_fetch_gl_zero_rows():
    tr = FakeTransport(total_records=0, raw_total=0)   # gl_total=1 -> 1 page, empty
    c = sage_client.SageClient(tr, CREDS)
    recs, err = c.fetch_gl("SID", date(2025, 1, 1), date(2025, 1, 31), page_size=2000)
    assert recs == []
    assert err is None
