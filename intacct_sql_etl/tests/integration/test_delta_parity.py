"""W-delta parity — delta(upsert)+sweep must converge to the SAME state as a full refresh
for the same Intacct truth (specs/13 §13.12). Offline, model-based.

A `ModeledConn` applies the black-box operations to an in-memory GL table:
  - EXEC test_AddGLDetailBulk @json  -> MERGE-upsert each record by (RECORDNO, LINE_NO)
  - DELETE ... WHERE Record_No=? AND Line_No=?  -> sweep single-key delete
  - DELETE FROM test_GLTable WHERE Entry_Date ...  -> full-window wipe
Anything referencing the AP table or the watermark table is ignored (GL is the parity subject).
"""
import json
from datetime import date

import pytest

from intacct_etl import sql_loader

pytestmark = pytest.mark.integration

CFG = {
    "gl_table": "test_GLTable", "apbill_table": "test_APBillTable",
    "watermark_table": "test_etl_watermark", "gl_delete_date_field": "Entry_Date",
    "gl_key_columns": ["Record_No", "Line_No"], "apbill_full_reload": True,
    "proc_add_gl": "test_AddGLDetailBulk", "proc_add_ap": "test_AddAPBillBulk",
    "proc_sync_links": "test_SyncAPLinksToGL", "proc_update_vendor": "test_UpdateVendorInfoInGL",
}
S, E = date(2026, 3, 1), date(2026, 6, 30)


class ModeledCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0
        self._last = ""

    def execute(self, sql, params=None):
        self._last = sql
        flat = sql.upper().replace(" ", "")
        if "EXEC" in flat and "ADDGLDETAILBULK" in flat:                 # load/upsert
            for r in json.loads(params[0]):
                key = (str(r.get("RECORDNO", "")), str(r.get("LINE_NO", "")))
                self.conn.table[key] = r.get("AMOUNT")
        elif flat.startswith("DELETEFROM") and "TEST_GLTABLE" in flat:
            if "RECORD_NO" in flat:                                      # sweep single-key delete
                self.conn.table.pop((str(params[0]), str(params[1])), None)
            else:                                                        # full-window wipe
                self.conn.table.clear()
        # AP-table ops + watermark ops: ignored (not the parity subject)

    def fetchall(self):
        if "TEST_GLTABLE" in self._last.upper().replace(" ", ""):
            return [ (k[0], k[1]) for k in self.conn.table.keys() ]
        return []

    def fetchone(self):
        return None

    def close(self):
        pass


class ModeledConn:
    def __init__(self, initial=None):
        self.table = dict(initial or {})
        self.commits = 0

    def cursor(self):
        return ModeledCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


# Current Intacct truth for the window (RECORDNO, LINE_NO -> AMOUNT)
TRUTH = [
    {"RECORDNO": "1", "LINE_NO": "1", "AMOUNT": "10"},
    {"RECORDNO": "2", "LINE_NO": "1", "AMOUNT": "20"},
    {"RECORDNO": "3", "LINE_NO": "1", "AMOUNT": "30"},
]
TRUTH_KEYS = {("1", "1"), ("2", "1"), ("3", "1")}


def test_full_refresh_from_empty_yields_truth():
    conn = ModeledConn()
    ld = sql_loader.SqlLoader(conn, CFG)
    ld.wipe_window(S, E)
    ld.load_gl_records(TRUTH)
    assert set(conn.table.keys()) == TRUTH_KEYS


def test_delta_plus_sweep_from_dirty_state_matches_full_refresh():
    # Dirty starting table: stale row (9/9), an out-of-date amount on 1/1, missing 3/1.
    dirty = {("1", "1"): "999", ("2", "1"): "20", ("9", "9"): "stale"}
    conn = ModeledConn(dict(dirty))
    ld = sql_loader.SqlLoader(conn, CFG)
    ld.load_gl_records(TRUTH)                 # upsert: add 3/1, correct 1/1, 2/1 unchanged
    ld.sweep_gl(TRUTH_KEYS, S, E)            # remove 9/9 (absent from Intacct)
    # same key set AND same values as a clean full refresh
    assert set(conn.table.keys()) == TRUTH_KEYS
    assert conn.table[("1", "1")] == "10"    # stale amount corrected by the upsert
    assert ("9", "9") not in conn.table      # stale row swept


def test_delta_sweep_is_idempotent_on_second_run():
    conn = ModeledConn()
    ld = sql_loader.SqlLoader(conn, CFG)
    ld.load_gl_records(TRUTH); ld.sweep_gl(TRUTH_KEYS, S, E)
    first = dict(conn.table)
    ld.load_gl_records(TRUTH); ld.sweep_gl(TRUTH_KEYS, S, E)   # run again, no changes
    assert conn.table == first == {("1", "1"): "10", ("2", "1"): "20", ("3", "1"): "30"}
