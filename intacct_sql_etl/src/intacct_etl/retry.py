"""Retry/backoff for transient I/O (specs/07; CR-18).

`make_retrier` returns a callable that runs a thunk with bounded retries and optional backoff.
The sleeper is injected so tests never actually wait, and config drives attempts/interval.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)


def make_retrier(*, attempts: int = 1, interval_s: float = 0, backoff: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep,
                 retryable: Optional[Callable[[Exception], bool]] = None):
    """Build a retrier: `retrier(thunk)` calls `thunk()` up to `attempts` times.

    Waits `interval_s` (then * backoff each round) between tries. `retryable(exc)` decides whether
    an exception is worth retrying (default: all). Re-raises the last exception if exhausted.
    """
    attempts = max(1, int(attempts))
    is_retryable = retryable or (lambda exc: True)

    def retrier(thunk: Callable):
        last_exc = None
        delay = interval_s
        for i in range(attempts):
            try:
                return thunk()
            except Exception as exc:               # noqa: BLE001 — bounded retry boundary
                last_exc = exc
                if i == attempts - 1 or not is_retryable(exc):
                    raise
                log.warning("retry %d/%d after error: %s", i + 1, attempts - 1, exc)
                if delay:
                    sleep(delay)
                delay *= backoff
        raise last_exc                              # pragma: no cover (loop always returns/raises)

    return retrier


def from_config(retry_cfg: dict, *, sleep: Callable[[float], None] = time.sleep):
    """Construct a retrier from the `retry` config section."""
    cfg = retry_cfg or {}
    return make_retrier(
        attempts=cfg.get("count", 1),
        interval_s=cfg.get("interval_s", 0),
        backoff=cfg.get("backoff", 1.0),
        sleep=sleep,
    )
