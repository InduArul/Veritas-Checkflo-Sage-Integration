"""W-delta unit tests — SageClient delta record fetch (specs/13 §13.4). Test-first."""
from datetime import datetime

from intacct_etl import sage_client

CREDS = {"sender_id": "S", "sender_password": "p", "user_id": "U",
         "user_password": "p", "company_id": "C"}
SINCE = datetime(2026, 7, 1, 7, 0, 0)
UNTIL = datetime(2026, 7, 2, 8, 59, 0)


class DeltaTransport:
    def __init__(self, n, raw_total, basis):
        self.records = [{"RECORDNO": str(i), "AMOUNT": "1.00"} for i in range(n)]
        self.raw_total = raw_total
        self.basis = basis
        self.page_requests = []
        self.probe_xml = None

    def __call__(self, xml):
        if "getAPISession" in xml:
            return {"session_id": "SID"}
        if "<pagesize>1</pagesize>" in xml:
            self.probe_xml = xml
            nr = self.raw_total if self.basis == "numremaining" else None
            tc = self.raw_total if self.basis == "totalcount" else None
            return {"status_code": 200, "numremaining": nr, "totalcount": tc,
                    "records": None, "error": None}
        off = int(xml.split("<offset>")[1].split("</offset>")[0])
        self.page_requests.append(off)
        return {"status_code": 200, "records": self.records[off:off + 2000], "error": None}


def test_fetch_gl_delta_pages_all_and_uses_whenmodified():
    tr = DeltaTransport(3000, raw_total=2999, basis="numremaining")
    c = sage_client.SageClient(tr, CREDS)
    recs, err = c.fetch_gl_delta("SID", SINCE, UNTIL, page_size=2000)
    assert err is None and len(recs) == 3000
    assert tr.page_requests == [0, 2000]
    assert "WHENMODIFIED" in tr.probe_xml and "07/01/2026 07:00:00" in tr.probe_xml


def test_fetch_ap_delta_uses_totalcount():
    tr = DeltaTransport(5, raw_total=5, basis="totalcount")
    c = sage_client.SageClient(tr, CREDS)
    recs, err = c.fetch_ap_delta("SID", SINCE, UNTIL, page_size=2000)
    assert err is None and len(recs) == 5
    assert "<object>APBILL</object>" in tr.probe_xml
