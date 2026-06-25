"""Sage offset/pagesize pagination math (specs/02 §FR-EXT-4). Pure functions.

GL count basis = numremaining + 1 (probe at pagesize 1).
AP count basis = totalcount (probe at pagesize 1).
"""
from __future__ import annotations

import math

DEFAULT_PAGE_SIZE = 2000


def gl_total(numremaining: int) -> int:
    """GL total records from the GL probe: numremaining + 1 (Make module 17)."""
    return int(numremaining) + 1


def ap_total(totalcount: int) -> int:
    """AP total records from the AP probe: totalcount (Make module 28)."""
    return int(totalcount)


def page_count(total: int, page_size: int = DEFAULT_PAGE_SIZE) -> int:
    """Number of pages to fetch. `1` if total <= page_size, else ceil(total/page_size).

    Matches Make `if(total<=2000;1;ceil(total/2000))`; never returns 0 (empty pages are
    skipped downstream by the data-length guard).
    """
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    if total <= page_size:
        return 1
    return math.ceil(total / page_size)


def offsets(n_pages: int, page_size: int = DEFAULT_PAGE_SIZE) -> list[int]:
    """Offsets to request: [0, page_size, 2*page_size, ...] of length `n_pages`.

    (`n_pages` is the value returned by `page_count`; renamed from `page_count` to avoid
    shadowing the module-level function — CR-10.)
    """
    return [i * page_size for i in range(n_pages)]
