"""W-delta unit tests — watermark control table (specs/13 §13.3). Test-first.

The watermark is a per-(environment, object) datetime bookmark stored in a SQL control
table. `get` reads it; `set` upserts it. All identifiers come from config and are validated;
all values are bound parameters. Connection is injected (no real driver in unit tests).
"""
from datetime import datetime

import pytest

from intacct_etl import watermark
from intacct_etl.config import ConfigError

WM_TABLE = "dbo.test_etl_watermark"


class FakeCursor:
    def __init__(self, row=None):
        self.calls = []
        self._row = row

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        return self._row

    def close(self):
        pass


class FakeConnection:
    def __init__(self, row=None):
        self.cur = FakeCursor(row)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_get_watermark_returns_stored_value():
    wm = datetime(2026, 6, 1, 8, 59, 0)
    conn = FakeConnection(row=(wm,))
    got = watermark.get_watermark(conn, WM_TABLE, "Evergreen", "GL")
    assert got == wm
    sql, params = conn.cur.calls[0]
    assert "SELECT" in sql.upper() and WM_TABLE in sql
    assert "?" in sql                                  # bound params, not interpolated
    assert params == ("Evergreen", "GL")


def test_get_watermark_none_when_absent():
    conn = FakeConnection(row=None)
    assert watermark.get_watermark(conn, WM_TABLE, "Evergreen", "AP") is None


def test_set_watermark_upserts_with_bound_params_and_commits():
    conn = FakeConnection()
    wm = datetime(2026, 7, 2, 1, 0, 0)
    watermark.set_watermark(conn, WM_TABLE, "Evergreen", "GL", wm,
                            now=datetime(2026, 7, 2, 1, 0, 5))
    sql, params = conn.cur.calls[0]
    up = sql.upper()
    assert WM_TABLE in sql
    assert "UPDATE" in up and "INSERT" in up          # upsert idiom (update-then-insert-if-absent)
    assert "@@ROWCOUNT" in up                          # insert only when the update matched nothing
    assert "?" in sql                                  # values are bound, never interpolated
    assert wm in params and "Evergreen" in params and "GL" in params
    assert conn.commits == 1


def test_set_watermark_no_commit_defers():
    conn = FakeConnection()
    watermark.set_watermark(conn, WM_TABLE, "Evergreen", "GL",
                            datetime(2026, 7, 2, 1, 0, 0), commit=False,
                            now=datetime(2026, 7, 2, 1, 0, 0))
    assert conn.commits == 0


def test_create_table_ddl_is_idempotent_and_test_prefixed():
    ddl = watermark.create_table_sql(WM_TABLE)
    up = ddl.upper()
    assert WM_TABLE in ddl
    assert "IF OBJECT_ID" in up or "IF NOT EXISTS" in up   # idempotent create
    assert "ENVIRONMENT" in up and "WATERMARK" in up


def test_identifiers_are_validated():
    conn = FakeConnection(row=None)
    with pytest.raises(ConfigError):
        watermark.get_watermark(conn, "dbo.test_wm; DROP TABLE x", "E", "GL")
