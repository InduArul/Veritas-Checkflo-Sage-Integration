"""SQL connection-retry for per-environment processing (reconnect-and-retry).

A transient DB connection failure (e.g. TCP 10060 / '08S01' communication-link failure) killed a
whole environment's seed with no retry (2026-07-15 cutover). The env body is idempotent
(Source_Env-scoped wipe + reload), so on a connection-class failure we reconnect and redo the env,
up to `sql.connection_retry.count` times, waiting `interval_s` between attempts. Data/logic errors
must NOT retry.
"""
from intacct_etl import orchestrator as orch

# The exact shape recorded when the 2026-07-15 Evergreen connection died mid-seed.
CONN_ERR = ("('08S01', '[08S01] [Microsoft][ODBC Driver 18 for SQL Server]"
            "TCP Provider: Error code 0x274C (10060) (SQLExecDirectW)')")
COMM_LINK = "('08S01', '[08S01] [Microsoft][ODBC Driver 18 for SQL Server]Communication link failure (0) (SQLEndTran)')"


def _entry(error=None, rollback_error=None):
    e = orch._new_entry()
    e["error"] = error
    e["rollback_error"] = rollback_error
    return e


# ---- _is_connection_error -------------------------------------------------------------------
def test_is_connection_error_detects_tcp_and_comm_link():
    assert orch._is_connection_error(_entry(error=CONN_ERR)) is True
    assert orch._is_connection_error(_entry(rollback_error=COMM_LINK)) is True


def test_is_connection_error_ignores_data_and_logic_errors():
    assert orch._is_connection_error(_entry(error="('23000', 'Violation of UNIQUE KEY constraint')")) is False
    assert orch._is_connection_error(_entry(error="Sage login failed: bad creds")) is False
    assert orch._is_connection_error(_entry()) is False  # no error at all


# ---- _process_env_with_retry ----------------------------------------------------------------
def test_retry_reconnects_on_connection_error_then_succeeds():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return (_entry(error=CONN_ERR), "audit1") if calls["n"] == 1 else (_entry(), "audit2")

    slept = []
    entry, audit = orch._process_env_with_retry(
        fn, retry_count=3, retry_interval=120, sleep=slept.append, name="Evergreen")
    assert calls["n"] == 2            # failed once, succeeded on reconnect
    assert slept == [120]             # waited exactly the configured interval, once
    assert entry["error"] is None
    assert audit == "audit2"


def test_retry_does_not_retry_a_non_connection_error():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return _entry(error="('23000', 'UNIQUE KEY violation')"), "audit"

    slept = []
    entry, audit = orch._process_env_with_retry(
        fn, retry_count=3, retry_interval=120, sleep=slept.append, name="X")
    assert calls["n"] == 1            # no retry on a data error
    assert slept == []


def test_retry_gives_up_after_count_and_returns_last_result():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return _entry(error=CONN_ERR), "audit"

    slept = []
    entry, audit = orch._process_env_with_retry(
        fn, retry_count=3, retry_interval=120, sleep=slept.append, name="X")
    assert calls["n"] == 4           # 1 initial + 3 retries
    assert slept == [120, 120, 120]
    assert orch._is_connection_error(entry)   # final (still-failed) result returned


def test_retry_count_zero_means_single_attempt():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return _entry(error=CONN_ERR), "audit"

    slept = []
    entry, audit = orch._process_env_with_retry(
        fn, retry_count=0, retry_interval=120, sleep=slept.append, name="X")
    assert calls["n"] == 1
    assert slept == []


# ---- config plumbing ------------------------------------------------------------------------
def test_connection_retry_cfg_reads_values_and_defaults():
    assert orch._connection_retry_cfg({"sql": {"connection_retry": {"count": 3, "interval_s": 120}}}) == (3, 120.0)
    assert orch._connection_retry_cfg({"sql": {}}) == (0, 120.0)   # default off, 120s interval
    assert orch._connection_retry_cfg({}) == (0, 120.0)
