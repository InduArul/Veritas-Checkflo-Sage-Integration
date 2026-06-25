"""Retry/backoff utility (CR-18). Sleeper injected so tests never wait."""
import pytest

from intacct_etl import retry


def test_returns_value_without_retry_on_success():
    calls = []
    r = retry.make_retrier(attempts=3, interval_s=5, sleep=lambda s: None)
    assert r(lambda: calls.append(1) or "ok") == "ok"
    assert len(calls) == 1


def test_retries_then_succeeds():
    state = {"n": 0}
    slept = []

    def flaky():
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError("transient")
        return "done"

    r = retry.make_retrier(attempts=5, interval_s=2, backoff=2.0, sleep=slept.append)
    assert r(flaky) == "done"
    assert state["n"] == 3
    assert slept == [2, 4]            # backoff applied between the 2 retries


def test_raises_after_exhausting_attempts():
    r = retry.make_retrier(attempts=2, interval_s=0, sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        r(lambda: (_ for _ in ()).throw(RuntimeError("always")))


def test_non_retryable_raises_immediately():
    calls = []

    def boom():
        calls.append(1)
        raise ValueError("fatal")

    r = retry.make_retrier(attempts=5, interval_s=1, sleep=lambda s: None,
                           retryable=lambda e: not isinstance(e, ValueError))
    with pytest.raises(ValueError):
        r(boom)
    assert len(calls) == 1            # not retried
