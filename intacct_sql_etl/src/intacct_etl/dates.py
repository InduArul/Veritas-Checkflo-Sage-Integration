"""Run-type, refresh-window, and month-slicing logic.

Pure functions (no I/O). Ports the Make mainflow date math (specs/05 §5.2/5.5).
All windows are inclusive [start, end]; dates are `datetime.date`.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

DAILY = "daily"
MONTHLY = "monthly"


def derive_run_type(today: date) -> str:
    """`monthly` on the 1st of the month, else `daily` (Make run_type 2 vs 1)."""
    return MONTHLY if today.day == 1 else DAILY


def last_day_of_month(d: date) -> date:
    last = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last)


def first_day_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)


def subtract_months(d: date, n: int) -> date:
    """Shift back `n` months; clamp day to the target month's last day."""
    month_index = (d.year * 12 + (d.month - 1)) - n
    year, month = divmod(month_index, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last))


def add_months(d: date, n: int) -> date:
    return subtract_months(d, -n)


def derive_window(today: date, daily_lookback_months: int = 3) -> tuple[date, date]:
    """Refresh window for a run (specs/05 §5.2).

    end   = last day of the current month (Make `Today_date`).
    start = monthly (today is the 1st): Jan 1 of last year.
            daily  (otherwise):         1st of the month `daily_lookback_months` ago.
    """
    end = last_day_of_month(today)
    if derive_run_type(today) == MONTHLY:
        start = date(today.year - 1, 1, 1)
    else:
        start = first_day_of_month(subtract_months(today, daily_lookback_months))
    return start, end


def month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """Contiguous calendar-month windows tiling [start, end].

    First chunk begins at `start`, last ends at `end`; intermediate chunks are full
    calendar months. Each next start = previous end + 1 day (no gap, no overlap).
    Parity-equivalent to the Make slicing loop (same union range -> same loaded rows;
    see docs/design/W0_design.md).
    """
    if start > end:
        return []
    chunks: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        chunk_end = min(last_day_of_month(cur), end)
        chunks.append((cur, chunk_end))
        cur = add_days(chunk_end, 1)
    return chunks


def month_chunks_dt(start_dt: datetime, end_dt: datetime) -> list[tuple[datetime, datetime]]:
    """Contiguous calendar-month sub-intervals tiling the half-open range ``(start_dt, end_dt]``
    on a *datetime* axis (specs/13 §13.16 — the delta-upsert ``WHENMODIFIED`` axis).

    Boundaries are shared (each next lower == previous upper) and sit at calendar-month starts
    (``00:00:00``), so the delta builder's strict ``> lower`` / ``<= upper`` filter partitions the
    range **exactly** — no gap, no double-count. The first chunk begins at ``start_dt`` and the last
    ends at ``end_dt`` (both preserved exactly). Returns ``[]`` when ``start_dt >= end_dt`` (an empty
    or inverted range has nothing to process under the strict ``> start`` filter).
    """
    if start_dt >= end_dt:
        return []
    chunks: list[tuple[datetime, datetime]] = []
    cur = start_dt
    while cur < end_dt:
        nxt = (datetime(cur.year + 1, 1, 1) if cur.month == 12
               else datetime(cur.year, cur.month + 1, 1))
        upper = min(nxt, end_dt)
        chunks.append((cur, upper))
        cur = upper
    return chunks


def group_chunks(chunks, n: int):
    """Merge every ``n`` consecutive ``(start, end)`` chunks into one wider chunk (``run.chunk_months``).

    ``n <= 1`` returns the chunks unchanged (one calendar month each — the tightest memory bound,
    specs/13 §13.16). Larger ``n`` trades a looser peak-memory bound for fewer Sage round-trips.
    Each merged chunk keeps the first sub-chunk's start and the last sub-chunk's end, so the union
    range — and therefore the rows processed — is identical regardless of ``n``.
    """
    chunks = list(chunks)
    if n <= 1 or not chunks:
        return chunks
    return [(chunks[i][0], chunks[min(i + n, len(chunks)) - 1][1])
            for i in range(0, len(chunks), n)]
