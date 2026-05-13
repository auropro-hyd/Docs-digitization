"""Pin the page-range syntax matching the client reference PDF.

Examples observed in the reference:

  * ``PAGE:103`` (single)
  * ``PAGE:6, 9, 31`` (sparse, < MIN_RANGE_RUN)
  * ``PAGE:36 to 42`` (contiguous run of 7)
  * ``PAGE:6 to 34`` (contiguous run of 29)

The threshold for switching from comma-separated to ``N to M`` is
``MIN_RANGE_RUN`` (=3). Below the threshold the run stays
comma-separated; at/above it collapses to a range.
"""

from __future__ import annotations

import pytest

from app.compliance.report_renderer.page_formatter import (
    MIN_RANGE_RUN,
    format_pages,
)


@pytest.mark.parametrize("pages,expected", [
    ([], ""),
    ([103], "PAGE:103"),
    ([6, 9, 31], "PAGE:6, 9, 31"),
    ([36, 37, 38, 39, 40, 41, 42], "PAGE:36 to 42"),
    ([6, 7, 8, 9, 10, 11, 12, 13], "PAGE:6 to 13"),
    # Mixed: sparse + run.
    ([1, 5, 6, 7, 8, 9, 10], "PAGE:1, 5 to 10"),
    # Two consecutive (one short of threshold) stays comma-separated.
    ([1, 2], "PAGE:1, 2"),
])
def test_format_pages_examples_from_reference(pages, expected) -> None:
    assert format_pages(pages) == expected


def test_format_pages_dedupes_and_sorts() -> None:
    """Input may arrive unsorted with duplicates (multiple findings
    on the same page). Output is sorted + de-duped."""

    assert format_pages([9, 6, 9, 31, 9]) == "PAGE:6, 9, 31"


def test_format_pages_ignores_invalid_entries() -> None:
    """Page 0 / negative pages get filtered (1-indexed page schema)."""

    assert format_pages([0, -1, 5]) == "PAGE:5"


def test_min_range_run_pin() -> None:
    """3 contiguous → range; 2 contiguous → comma-separated."""

    assert MIN_RANGE_RUN == 3
    assert format_pages([10, 11, 12]) == "PAGE:10 to 12"
    assert format_pages([10, 11]) == "PAGE:10, 11"


def test_long_contiguous_range_matches_reference() -> None:
    """Reference exemplar 'All steps followed within standard limits'
    had ``PAGE:6 to 34`` — a 29-page contiguous run."""

    assert format_pages(list(range(6, 35))) == "PAGE:6 to 34"
