"""W0 unit tests — pagination math (specs/02 §FR-EXT-4). Golden values from Make samples."""
import pytest

from intacct_etl import pagination


@pytest.mark.parametrize("numremaining,expected", [(0, 1), (1, 2), (580050, 580051)])
def test_gl_total(numremaining, expected):
    assert pagination.gl_total(numremaining) == expected


@pytest.mark.parametrize("totalcount,expected", [(0, 0), (1, 1), (8321, 8321)])
def test_ap_total(totalcount, expected):
    assert pagination.ap_total(totalcount) == expected


@pytest.mark.parametrize(
    "total,expected",
    [(0, 1), (1, 1), (2000, 1), (2001, 2), (4000, 2), (4001, 3), (580051, 291)],
)
def test_page_count_default_size(total, expected):
    assert pagination.page_count(total) == expected


def test_page_count_custom_size():
    assert pagination.page_count(250, page_size=100) == 3
    assert pagination.page_count(100, page_size=100) == 1


def test_offsets():
    assert pagination.offsets(5) == [0, 2000, 4000, 6000, 8000]
    assert pagination.offsets(1) == [0]
    assert pagination.offsets(3, page_size=100) == [0, 100, 200]


def test_ap_sample_end_to_end():
    total = pagination.ap_total(8321)
    pc = pagination.page_count(total)
    assert pc == 5
    assert pagination.offsets(pc) == [0, 2000, 4000, 6000, 8000]
