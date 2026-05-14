"""Page-header parsing + boundary-unit grouping for Spec 011 / US1.

Pharmaceutical forms print ``Page X of Y`` in their running header.
That's the single highest-confidence document-boundary signal we
have: the form designer literally typed how many pages the form
has. The LLM-driven segmentation is supposed to use this signal
but frequently misses it (Akhilesh's 2026-05-13 voice notes:
"page 1 of 3 means the document has 3 pages, but segmentation is
splitting it into two or three").

This module is the deterministic post-process counterpart:

* :func:`parse_page_headers` — regex-scan the first ~200 chars of
  each page's OCR markdown for ``Page X of Y`` (and tolerated
  typo variants). Returns one :class:`PageHeader` per page that
  carries a detectable header.
* :func:`group_boundary_units` — group consecutive pages whose
  headers attest they belong to the same logical sub-document
  (constant ``Y``, ``X`` strictly increasing from 1).

The output (a list of :class:`BoundaryUnit`) is fed to
:func:`app.compliance.segmentation.merge_split_by_boundary` which
merges LLM-split sections inside one unit and splits LLM-glued
sections across unit transitions.

Pure: no I/O, no LLM, no logging. The caller decides how to react
to low-confidence parses (see ``confidence`` on PageHeader).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


# How many chars of the page's markdown to scan for the running
# header. Real headers sit at the very top; 200 chars is enough to
# cover "<logo line>\n<title line>\nPage X of Y" without dragging
# in body text that might mention page numbers (e.g. cross-
# references in a table of contents).
_HEADER_SCAN_LIMIT: int = 200


# Two tolerance tiers:
#
# * Strict — the canonical form. Confidence 1.0.
# * Tolerant — accepts OCR-noisy variants ("Pege", "pg", "/").
#   Confidence 0.7 so the caller can surface low-confidence parses
#   for HITL review (``segmentation.header_low_confidence`` event).
#
# The tolerant regex is intentionally NOT greedy — it requires the
# digits to be word-boundaries so a phrase like ``page 1, item 3``
# doesn't accidentally match as "page 1 of 3".
_STRICT_PATTERN = re.compile(
    r"\bPage\s+(\d{1,3})\s+of\s+(\d{1,3})\b",
    re.IGNORECASE,
)
_TOLERANT_PATTERN = re.compile(
    r"\b(?:[Pp][ae]?ge?|pg)\s*\.?\s*(\d{1,3})\s*(?:of|/|\\)\s*(\d{1,3})\b",
)


@dataclass(frozen=True)
class PageHeader:
    """One ``Page X of Y`` parse result from a single page's
    OCR markdown header zone."""

    page_num: int
    """1-indexed absolute page number in the packet."""

    x: int
    """The X in 'Page X of Y' — within-document index (1-indexed)."""

    y: int
    """The Y in 'Page X of Y' — total pages in that sub-document."""

    raw: str
    """The matched text, for telemetry / HITL display."""

    confidence: float
    """1.0 for the canonical pattern, 0.7 for OCR-tolerated variants."""


@dataclass(frozen=True)
class BoundaryUnit:
    """A contiguous run of pages whose headers attest they belong
    to the same logical sub-document."""

    start_page: int
    end_page: int
    expected_pages: int
    """The ``Y`` value all pages in the unit share."""

    header_count: int
    """Pages in the run that actually carried a parseable header.
    May be less than ``end_page - start_page + 1`` when some pages
    lacked the header but were inferred to belong via X-continuity.
    """


def parse_page_headers(extractions: Iterable[dict]) -> list[PageHeader]:
    """Scan each page's markdown for ``Page X of Y``.

    Returns one :class:`PageHeader` per page where the regex matched.
    Pages without a header (or with malformed text) simply don't
    appear in the output — the caller treats their absence as "no
    header attested" rather than "wrong".

    The scan window is the FIRST :data:`_HEADER_SCAN_LIMIT` chars of
    each page's markdown. Real running headers sit there; scanning
    deeper risks false-positives on body text that mentions page
    numbers (cross-references in tables of contents, regulatory
    text quoting "Page 1 of FDA Form 483", etc.).
    """

    out: list[PageHeader] = []
    for ext in extractions:
        page_num_raw = ext.get("page_num")
        markdown = ext.get("markdown") or ""
        if not isinstance(page_num_raw, int) or page_num_raw < 1:
            continue
        if not markdown:
            continue

        window = markdown[:_HEADER_SCAN_LIMIT]
        match = _STRICT_PATTERN.search(window)
        confidence = 1.0
        if match is None:
            match = _TOLERANT_PATTERN.search(window)
            confidence = 0.7
        if match is None:
            continue

        try:
            x = int(match.group(1))
            y = int(match.group(2))
        except (TypeError, ValueError):
            continue
        if x < 1 or y < 1 or x > y:
            # Nonsensical pair — better to discard than to feed
            # the boundary grouper a confusing signal.
            continue

        out.append(PageHeader(
            page_num=page_num_raw,
            x=x,
            y=y,
            raw=match.group(0),
            confidence=confidence,
        ))
    return out


def group_boundary_units(headers: Iterable[PageHeader]) -> list[BoundaryUnit]:
    """Group consecutive pages into boundary units.

    A "unit" is a contiguous run of pages where:

    * ``Y`` (the total-pages count) is the same on every page
      that carries a header, AND
    * ``X`` is strictly increasing across consecutive pages that
      carry a header (gaps fine — a page without a header is
      assumed to belong to the same unit if the surrounding pages
      agree), AND
    * The run starts on a page whose ``X == 1`` (so we don't
      accidentally split a unit mid-form when the first page of
      that form happened to not have a parseable header).

    Pages without a header that fall BETWEEN attested header pages
    of the same unit are absorbed into the unit. Pages without a
    header that fall OUTSIDE any unit (e.g. a single image page
    between two distinct boundary units) are simply not emitted —
    the rest of the segmentation pipeline (gap-fill, LLM-driven
    classification) handles them.

    Returns the units in ``start_page`` order.
    """

    sorted_headers = sorted(headers, key=lambda h: h.page_num)
    if not sorted_headers:
        return []

    units: list[BoundaryUnit] = []
    # State for the in-progress unit.
    unit_start_page: int | None = None
    unit_end_page: int | None = None
    unit_y: int | None = None
    unit_last_x: int | None = None
    unit_header_count: int = 0

    def flush() -> None:
        if (
            unit_start_page is not None
            and unit_end_page is not None
            and unit_y is not None
        ):
            units.append(BoundaryUnit(
                start_page=unit_start_page,
                end_page=unit_end_page,
                expected_pages=unit_y,
                header_count=unit_header_count,
            ))

    for h in sorted_headers:
        if unit_start_page is None:
            # Start a new unit IFF this header begins at X==1. A
            # mid-doc page with X==3 isn't a unit start; we wait
            # for the next X==1.
            if h.x == 1:
                unit_start_page = h.page_num
                unit_end_page = h.page_num
                unit_y = h.y
                unit_last_x = 1
                unit_header_count = 1
            # else: skip — we're between units, no anchor yet.
            continue

        assert unit_y is not None and unit_last_x is not None

        # Continuation conditions: same Y, X strictly greater, page
        # number adjacent to the current unit's end (with tolerance
        # for header-less pages between).
        same_y = h.y == unit_y
        x_increases = h.x > unit_last_x

        if same_y and x_increases:
            unit_end_page = h.page_num
            unit_last_x = h.x
            unit_header_count += 1
            continue

        # Y changed or X reset → close the current unit and start
        # a new one if the new header is at X==1.
        flush()
        if h.x == 1:
            unit_start_page = h.page_num
            unit_end_page = h.page_num
            unit_y = h.y
            unit_last_x = 1
            unit_header_count = 1
        else:
            unit_start_page = None
            unit_end_page = None
            unit_y = None
            unit_last_x = None
            unit_header_count = 0

    flush()
    return units
