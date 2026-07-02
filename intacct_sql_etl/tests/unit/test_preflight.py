"""specs/14 §14.4 — read-only SQL pre-flight check. Test-first; offline."""
from intacct_etl import db


class Cur:
    def __init__(self):
        self.executed = None

    def execute(self, sql, params=None):
        self.executed = sql

    def fetchone(self):
        return (1,)

    def close(self):
        pass


class Conn:
    def __init__(self):
        self.closed = False
        self.cur = Cur()

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


def test_preflight_ok_runs_select_1_and_closes():
    conn = Conn()

    def connect(sql_cfg, secrets):
        return conn

    assert db.preflight({"server": "x", "database": "d"}, {}, connect=connect) is None
    assert conn.cur.executed == "SELECT 1"
    assert conn.closed is True


def test_preflight_connect_failure_returns_error():
    def connect(sql_cfg, secrets):
        raise RuntimeError("Login timeout expired")

    err = db.preflight({"server": "x"}, {}, connect=connect)
    assert err is not None and "Login timeout" in err


def test_preflight_query_failure_returns_error():
    class BadCur(Cur):
        def execute(self, sql, params=None):
            raise RuntimeError("Login failed for user")

    class BadConn(Conn):
        def cursor(self):
            return BadCur()

    conn = BadConn()
    err = db.preflight({"server": "x"}, {}, connect=lambda c, s: conn)
    assert "Login failed" in err
    assert conn.closed is True                     # still closed on failure
