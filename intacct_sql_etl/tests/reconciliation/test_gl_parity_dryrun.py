"""W1 Evaluate (offline dry-run parity) — specs/11.

Stand-in for the live parity gate: a known GL dataset is extracted+loaded by the slice, then
reconciled against the same dataset (acting as 'Make prod'). Live reconciliation vs Make prod is
deferred to creds (OQ-11). Marked `reconciliation`.
"""
from datetime import date

import json

import pytest

from intacct_etl import orchestrator, reconcile, sage_client, sql_loader

pytestmark = pytest.mark.reconciliation

CREDS = {"sender_id": "S", "sender_password": "p", "user_id": "U",
         "user_password": "p", "company_id": "C"}
KEYS = ["RECORDNO"]
TEST_SQL_CFG = {
    "gl_table": "test_GLTable", "apbill_table": "test_APBillTable",
    "proc_add_gl": "test_AddGLDetailBulk", "proc_add_ap": "test_AddAPBillBulk",
    "proc_sync_links": "test_SyncAPLinksToGL", "proc_update_vendor": "test_UpdateVendorInfoInGL",
}


class FakeTransport:
    def __init__(self, total_records, raw_total, page_size=2000):
        self.page_size = page_size
        self.dataset = [{"RECORDNO": str(i), "AMOUNT": f"{i}.00"} for i in range(total_records)]
        self.raw_total = raw_total

    def __call__(self, xml):
        if "getAPISession" in xml:
            return {"session_id": "SID"}
        if "<pagesize>1</pagesize>" in xml:
            return {"status_code": 200, "numremaining": self.raw_total, "records": None, "error": None}
        off = int(xml.split("<offset>")[1].split("</offset>")[0])
        return {"status_code": 200, "records": self.dataset[off:off + self.page_size], "error": None}


class CapturingLoader(sql_loader.SqlLoader):
    """Captures the rows actually loaded (decoded from the bound JSON params)."""

    def __init__(self, sql_cfg):
        self.loaded_rows = []
        super().__init__(_RecordingConn(self.loaded_rows), sql_cfg)


class _RecordingCursor:
    def __init__(self, sink):
        self.sink = sink

    def execute(self, sql, params=None):
        if params:
            self.sink.extend(json.loads(params[0]))

    def close(self):
        pass


class _RecordingConn:
    def __init__(self, sink):
        self._cur = _RecordingCursor(sink)

    def cursor(self):
        return self._cur

    def commit(self):
        pass

    def rollback(self):
        pass


def test_gl_dryrun_parity_passes():
    make_prod = [{"RECORDNO": str(i), "AMOUNT": f"{i}.00"} for i in range(5000)]
    tr = FakeTransport(total_records=5000, raw_total=4999)
    client = sage_client.SageClient(tr, CREDS)
    loader = CapturingLoader(TEST_SQL_CFG)

    result = orchestrator.run_gl_for_window(
        CREDS, date(2025, 1, 1), date(2025, 1, 31),
        sage_client=client, loader=loader, page_size=2000,
    )
    assert result["gl_records"] == 5000
    assert result["gl_loaded"] == 5000
    assert result["gl_error"] is None

    rec = reconcile.reconcile(make_prod, loader.loaded_rows, key_fields=KEYS)
    assert rec.passed, rec.sample_diffs


def test_gl_dryrun_parity_detects_drift():
    # utility loses a row vs 'Make prod' -> parity MUST fail
    make_prod = [{"RECORDNO": str(i), "AMOUNT": f"{i}.00"} for i in range(100)]
    tr = FakeTransport(total_records=99, raw_total=98)      # one row short
    client = sage_client.SageClient(tr, CREDS)
    loader = CapturingLoader(TEST_SQL_CFG)
    orchestrator.run_gl_for_window(CREDS, date(2025, 1, 1), date(2025, 1, 31),
                                   sage_client=client, loader=loader, page_size=2000)
    rec = reconcile.reconcile(make_prod, loader.loaded_rows, key_fields=KEYS)
    assert not rec.passed
