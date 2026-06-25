"""Run-type, refresh-window, and month-slicing logic.

Pure functions (no I/O). Ports the Make mainflow date math (specs/05 §5.2/5.5).
All windows are inclusive [start, end]; dates are `datetime.date`.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

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
