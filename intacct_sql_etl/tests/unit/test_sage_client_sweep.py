"""W-delta unit tests — SageClient key fetch for the sweep (specs/13 §13.5). Test-first."""
from datetime import date

from intacct_etl import sage_client

CREDS = {"sender_id": "S", "sender_password": "p", "user_id": "U",
         "user_password": "p", "company_id": "C"}


class KeysTransport:
    def __init__(self, n, raw_total, basis):
        self.records = [{"RECORDNO": str(i), "LINE_NO": "1"} for i in range(n)]
        self.raw_total = raw_total
        self.basis = basis                     # "numremaining" (GL) | "totalcount" (AP)
        self.page_requests = []

    def __call__(self, xml):
        if "getAPISession" in xml:
            return {"session_id": "SID"}
        if "<pagesize>1</pagesize>" in xml:
            nr = self.raw_total if self.basis == "numremaining" else None
            tc = self.raw_total if self.basis == "totalcount" else None
            return {"status_code": 200, "numremaining": nr, "totalcount": tc,
                    "records": None, "error": None}
        off = int(xml.split("<offset>")[1].split("</offset>")[0])
        self.page_requests.append(off)
        return {"status_code": 200, "records": self.records[off:off + 2000], "error": None}


def test_fetch_gl_keys_pages_all():
    tr = KeysTransport(2500, raw_total=2499, basis="numremaining")     # 2500 -> 2 pages
    c = sage_client.SageClient(tr, CREDS)
    recs, err = c.fetch_gl_keys("SID", date(2026, 3, 1), date(2026, 6, 30), page_size=2000)
    assert err is None and len(recs) == 2500
    assert tr.page_requests == [0, 2000]


def test_fetch_ap_keys_uses_totalcount():
    tr = KeysTransport(10, raw_total=10, basis="totalcount")
    c = sage_client.SageClient(tr, CREDS)
    recs, err = c.fetch_ap_keys("SID", date(2024, 1, 1), date(2026, 7, 2), page_size=2000)
    assert err is None and len(recs) == 10
    assert tr.page_requests == [0]
