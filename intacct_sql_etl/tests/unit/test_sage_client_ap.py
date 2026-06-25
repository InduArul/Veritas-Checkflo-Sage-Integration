"""W2 unit tests — SageClient.fetch_ap (specs/02). AP uses totalcount (not numremaining+1),
probe field set differs from page field set. Test-first."""
from datetime import date

from intacct_etl import sage_client

CREDS = {"sender_id": "S", "sender_password": "p", "user_id": "U",
         "user_password": "p", "company_id": "C"}


class FakeAPTransport:
    def __init__(self, total_records, page_size=2000):
        self.page_size = page_size
        self.dataset = [{"RECORDNO": str(i), "VENDORID": f"V{i}"} for i in range(total_records)]
        self.totalcount = total_records          # AP probe reports totalcount directly
        self.probe_fields_seen = None
        self.page_fields_seen = None

    def __call__(self, xml):
        if "getAPISession" in xml:
            return {"session_id": "SID"}
        if "<pagesize>1</pagesize>" in xml:
            self.probe_fields_seen = "VENDORID" in xml
            return {"status_code": 200, "totalcount": self.totalcount, "records": None, "error": None}
        self.page_fields_seen = "VENDORID" in xml
        off = int(xml.split("<offset>")[1].split("</offset>")[0])
        return {"status_code": 200, "records": self.dataset[off:off + self.page_size], "error": None}


def test_fetch_ap_uses_totalcount_basis_and_aggregates():
    # 8321 records -> ap_total=8321 -> 5 pages
    tr = FakeAPTransport(total_records=8321)
    c = sage_client.SageClient(tr, CREDS)
    recs, err = c.fetch_ap("SID", date(2024, 1, 1), date(2024, 3, 31), page_size=2000)
    assert err is None
    assert len(recs) == 8321


def test_fetch_ap_probe_uses_4_fields_page_uses_7():
    tr = FakeAPTransport(total_records=10)
    c = sage_client.SageClient(tr, CREDS)
    c.fetch_ap("SID", date(2024, 1, 1), date(2024, 3, 31), page_size=2000)
    assert tr.probe_fields_seen is False        # probe field set has no VENDORID
    assert tr.page_fields_seen is True          # page field set includes VENDORID


def test_fetch_ap_zero_rows():
    tr = FakeAPTransport(total_records=0)
    c = sage_client.SageClient(tr, CREDS)
    recs, err = c.fetch_ap("SID", date(2024, 1, 1), date(2024, 3, 31))
    assert recs == [] and err is None
