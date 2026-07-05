"""W0 unit tests — run-type, window derivation, month-slicing (specs/05 §5.2/5.5).
Written test-first (TDD). Golden values from Make blueprint samples (docs/design/W0_design.md)."""
from datetime import date, datetime

import pytest

from intacct_etl import dates


# ---- derive_run_type ----
@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 6, 1), "monthly"),   # 1st of month
        (date(2026, 4, 1), "monthly"),   # Make sample run
        (date(2026, 6, 24), "daily"),    # mid-month
        (date(2026, 2, 28), "daily"),
        (date(2027, 1, 31), "daily"),
    ],
)
def test_derive_run_type(today, expected):
    assert dates.derive_run_type(today) == expected


# ---- last_day_of_month ----
@pytest.mark.parametrize(
    "d,expected",
    [
        (date(2026, 6, 24), date(2026, 6, 30)),
        (date(2025, 2, 10), date(2025, 2, 28)),   # non-leap
        (date(2024, 2, 10), date(2024, 2, 29)),   # leap
        (date(2026, 12, 1), date(2026, 12, 31)),
    ],
)
def test_last_day_of_month(d, expected):
    assert dates.last_day_of_month(d) == expected


# ---- subtract_months ----
@pytest.mark.parametrize(
    "d,n,expected",
    [
        (date(2026, 6, 24), 3, date(2026, 3, 24)),
        (date(2026, 1, 15), 3, date(2025, 10, 15)),   # year wrap
        (date(2026, 3, 31), 1, date(2026, 2, 28)),    # day clamp
    ],
)
def test_subtract_months(d, n, expected):
    assert dates.subtract_months(d, n) == expected


# ---- derive_window (GOLDEN from Make samples) ----
def test_window_monthly_make_sample():
    # 8AM sample: now=2026-04-01 (1st) -> monthly, Jan-1-last-year .. end of current month
    start, end = dates.derive_window(date(2026, 4, 1))
    assert (start, end) == (date(2025, 1, 1), date(2026, 4, 30))


def test_window_daily():
    # mid-month -> 1st of (3 months ago) .. end of current month
    start, end = dates.derive_window(date(2026, 6, 24))
    assert (start, end) == (date(2026, 3, 1), date(2026, 6, 30))


def test_window_daily_year_wrap():
    start, end = dates.derive_window(date(2026, 1, 15))
    assert (start, end) == (date(2025, 10, 1), date(2026, 1, 31))


# ---- month_chunks ----
def test_month_chunks_daily_window():
    chunks = dates.month_chunks(date(2026, 3, 1), date(2026, 6, 30))
    assert chunks == [
        (date(2026, 3, 1), date(2026, 3, 31)),
        (date(2026, 4, 1), date(2026, 4, 30)),
        (date(2026, 5, 1), date(2026, 5, 31)),
        (date(2026, 6, 1), date(2026, 6, 30)),
    ]


def test_month_chunks_monthly_window_count():
    chunks = dates.month_chunks(date(2025, 1, 1), date(2026, 4, 30))
    assert len(chunks) == 16                       # Jan2025..Apr2026 inclusive
    assert chunks[0] == (date(2025, 1, 1), date(2025, 1, 31))
    assert chunks[-1] == (date(2026, 4, 1), date(2026, 4, 30))


def test_month_chunks_are_contiguous_no_gap_no_overlap():
    chunks = dates.month_chunks(date(2025, 1, 1), date(2026, 4, 30))
    for (s1, e1), (s2, e2) in zip(chunks, chunks[1:]):
        assert s2 == dates.add_days(e1, 1)         # next start = prev end + 1 day (Make invariant)
    # union covers exactly [start, end]
    assert chunks[0][0] == date(2025, 1, 1)
    assert chunks[-1][1] == date(2026, 4, 30)


def test_month_chunks_single_partial_month():
    # start not a 1st, end mid-month -> single clamped chunk
    chunks = dates.month_chunks(date(2026, 6, 10), date(2026, 6, 20))
    assert chunks == [(date(2026, 6, 10), date(2026, 6, 20))]


# ---- month_chunks_dt (specs/13 §13.16 — datetime tiling for the delta-upsert WHENMODIFIED axis) ----
# Half-open (start, end] partitioning with boundaries shared at calendar-month starts, so the
# builder's strict `> lower` / `<= upper` filter never gaps or double-counts a record.

def test_month_chunks_dt_crosses_month_boundary():
    chunks = dates.month_chunks_dt(datetime(2026, 6, 30, 23, 0), datetime(2026, 7, 2, 9, 0))
    assert chunks == [
        (datetime(2026, 6, 30, 23, 0), datetime(2026, 7, 1, 0, 0)),
        (datetime(2026, 7, 1, 0, 0), datetime(2026, 7, 2, 9, 0)),
    ]


def test_month_chunks_dt_within_single_month_is_one_chunk():
    chunks = dates.month_chunks_dt(datetime(2026, 7, 1, 10, 0), datetime(2026, 7, 2, 9, 0))
    assert chunks == [(datetime(2026, 7, 1, 10, 0), datetime(2026, 7, 2, 9, 0))]


def test_month_chunks_dt_wide_range_count_and_endpoints():
    chunks = dates.month_chunks_dt(datetime(2025, 1, 1, 0, 0), datetime(2026, 5, 1, 0, 0))
    assert len(chunks) == 16                                   # Jan2025..Apr2026 month-intervals
    assert chunks[0][0] == datetime(2025, 1, 1, 0, 0)
    assert chunks[-1][1] == datetime(2026, 5, 1, 0, 0)


def test_month_chunks_dt_contiguous_shared_boundaries():
    chunks = dates.month_chunks_dt(datetime(2025, 1, 1, 0, 0), datetime(2026, 5, 1, 0, 0))
    for (s1, e1), (s2, e2) in zip(chunks, chunks[1:]):
        assert s2 == e1                                        # shared boundary (no gap/overlap)
    assert chunks[0][0] == datetime(2025, 1, 1, 0, 0)
    assert chunks[-1][1] == datetime(2026, 5, 1, 0, 0)


@pytest.mark.parametrize("s,e", [
    (datetime(2026, 7, 2, 9, 0), datetime(2026, 7, 2, 9, 0)),   # equal -> nothing to process
    (datetime(2026, 7, 3, 0, 0), datetime(2026, 7, 2, 9, 0)),   # inverted
])
def test_month_chunks_dt_empty_when_start_not_before_end(s, e):
    assert dates.month_chunks_dt(s, e) == []


# ---- group_chunks (run.chunk_months) ----
def test_group_chunks_n1_is_identity():
    months = dates.month_chunks(date(2026, 1, 1), date(2026, 4, 30))
    assert dates.group_chunks(months, 1) == months


def test_group_chunks_merges_and_preserves_union():
    months = dates.month_chunks(date(2026, 1, 1), date(2026, 4, 30))   # 4 monthly chunks
    grouped = dates.group_chunks(months, 2)
    assert grouped == [(date(2026, 1, 1), date(2026, 2, 28)),
                       (date(2026, 3, 1), date(2026, 4, 30))]
    assert grouped[0][0] == months[0][0] and grouped[-1][1] == months[-1][1]   # union preserved


def test_group_chunks_ragged_last_group():
    months = dates.month_chunks(date(2026, 1, 1), date(2026, 3, 31))   # 3 chunks, n=2 -> 2 groups
    grouped = dates.group_chunks(months, 2)
    assert grouped == [(date(2026, 1, 1), date(2026, 2, 28)),
                       (date(2026, 3, 1), date(2026, 3, 31))]
