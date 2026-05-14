"""Tests for Spec 011 / US1 page-header parser + boundary-unit grouper.

Pins the deterministic detection of ``Page X of Y`` running
headers and the grouping logic that turns the per-page parses
into contiguous boundary units.
"""

from __future__ import annotations

import pytest

from app.compliance.segmentation_headers import (
    BoundaryUnit,
    PageHeader,
    group_boundary_units,
    parse_page_headers,
)


# ── parse_page_headers ─────────────────────────────────────────


def _ext(page_num: int, markdown: str) -> dict:
    return {"page_num": page_num, "markdown": markdown}


def test_parses_canonical_three_page_run() -> None:
    """Three consecutive pages with the canonical ``Page X of 3``
    header parse to three :class:`PageHeader` entries with
    confidence 1.0."""

    extractions = [
        _ext(30, "APITORIA PHARMA\nRaw Material Request\nPage 1 of 3\n\nMaterial..."),
        _ext(31, "APITORIA PHARMA\nRaw Material Request\nPage 2 of 3\n\nQty..."),
        _ext(32, "APITORIA PHARMA\nRaw Material Request\nPage 3 of 3\n\nSign..."),
    ]
    headers = parse_page_headers(extractions)
    assert [(h.page_num, h.x, h.y, h.confidence) for h in headers] == [
        (30, 1, 3, 1.0),
        (31, 2, 3, 1.0),
        (32, 3, 3, 1.0),
    ]


def test_tolerates_ocr_typo_with_lower_confidence() -> None:
    """OCR noise turns ``Page`` into ``Pege`` / ``pg`` / changes
    ``of`` to ``/``. The tolerant pattern still matches but the
    confidence drops to 0.7 so HITL can flag it."""

    extractions = [
        _ext(10, "Pege 1 of 2\n"),
        _ext(11, "pg 2 / 2\n"),
    ]
    headers = parse_page_headers(extractions)
    assert len(headers) == 2
    assert headers[0].confidence == 0.7
    assert headers[1].confidence == 0.7
    assert (headers[0].x, headers[0].y) == (1, 2)


def test_no_match_returns_empty_for_that_page() -> None:
    """Pages whose markdown carries no parseable header don't
    contribute to the output."""

    extractions = [
        _ext(1, "Cover sheet — Product Name: Sertraline"),
        _ext(2, "Page 1 of 3\nForm content"),
    ]
    headers = parse_page_headers(extractions)
    assert [h.page_num for h in headers] == [2]


def test_skips_headers_past_the_scan_window() -> None:
    """A ``Page X of Y`` mention deep in body text (cross-reference,
    quoted regulatory text) MUST NOT match — the scan window is
    bounded so we don't pick up body noise."""

    # Padding to push the marker past the 200-char scan window.
    padding = "x" * 220
    extractions = [_ext(5, f"{padding}\nPage 1 of 3 — referenced elsewhere")]
    assert parse_page_headers(extractions) == []


def test_rejects_nonsensical_xy_pairs() -> None:
    """``Page 5 of 3`` is structurally impossible — the page index
    can't exceed the total. Discarded to keep the grouper's input
    clean."""

    extractions = [_ext(1, "Page 5 of 3 — bad OCR")]
    assert parse_page_headers(extractions) == []


def test_rejects_zero_or_negative_values() -> None:
    extractions = [_ext(1, "Page 0 of 3"), _ext(2, "Page 1 of 0")]
    assert parse_page_headers(extractions) == []


def test_ignores_invalid_page_num() -> None:
    """An extraction with a missing or non-int ``page_num`` is
    skipped silently."""

    extractions = [
        {"page_num": None, "markdown": "Page 1 of 3"},
        {"markdown": "Page 1 of 3"},
        {"page_num": 0, "markdown": "Page 1 of 3"},
    ]
    assert parse_page_headers(extractions) == []


def test_case_insensitive_strict_pattern() -> None:
    """The strict regex is case-insensitive so ``PAGE 1 OF 3`` and
    ``page 1 of 3`` both hit confidence 1.0."""

    extractions = [_ext(1, "PAGE 1 OF 3"), _ext(2, "page 2 of 3")]
    headers = parse_page_headers(extractions)
    assert all(h.confidence == 1.0 for h in headers)


# ── group_boundary_units ────────────────────────────────────────


def _hdr(page_num: int, x: int, y: int, *, confidence: float = 1.0) -> PageHeader:
    return PageHeader(
        page_num=page_num, x=x, y=y,
        raw=f"Page {x} of {y}", confidence=confidence,
    )


def test_three_consecutive_pages_form_one_unit() -> None:
    """The canonical case: pages 30 / 31 / 32 with `1 of 3` ..
    `3 of 3` collapse into one boundary unit."""

    units = group_boundary_units([
        _hdr(30, 1, 3),
        _hdr(31, 2, 3),
        _hdr(32, 3, 3),
    ])
    assert units == [
        BoundaryUnit(start_page=30, end_page=32, expected_pages=3, header_count=3),
    ]


def test_y_change_splits_into_two_units() -> None:
    """Pages 1-3 with `Y=3` then 4-5 with `Y=2` form two distinct
    units."""

    units = group_boundary_units([
        _hdr(1, 1, 3), _hdr(2, 2, 3), _hdr(3, 3, 3),
        _hdr(4, 1, 2), _hdr(5, 2, 2),
    ])
    assert units == [
        BoundaryUnit(start_page=1, end_page=3, expected_pages=3, header_count=3),
        BoundaryUnit(start_page=4, end_page=5, expected_pages=2, header_count=2),
    ]


def test_single_page_unit() -> None:
    units = group_boundary_units([_hdr(50, 1, 1)])
    assert units == [
        BoundaryUnit(start_page=50, end_page=50, expected_pages=1, header_count=1),
    ]


def test_unit_must_start_at_x_equals_one() -> None:
    """A standalone header with ``X=2`` doesn't anchor a new unit
    — without ``X=1`` we have no idea where the form actually
    starts."""

    units = group_boundary_units([_hdr(5, 2, 3), _hdr(6, 3, 3)])
    assert units == []


def test_mid_doc_header_absent_pages_absorbed_into_unit() -> None:
    """Page 31 lacks a header (image-heavy page); pages 30 and 32
    have ``1 of 3`` and ``3 of 3``. Output is one unit covering
    pages 30-32 with ``header_count=2`` so the caller can flag
    missing-header coverage."""

    units = group_boundary_units([_hdr(30, 1, 3), _hdr(32, 3, 3)])
    assert units == [
        BoundaryUnit(start_page=30, end_page=32, expected_pages=3, header_count=2),
    ]


def test_x_reset_to_one_starts_new_unit() -> None:
    """Two consecutive 1-page forms (each ``Page 1 of 1``) sit on
    pages 10 and 11. The X reset on page 11 closes the unit at
    page 10 and starts a new one at page 11."""

    units = group_boundary_units([_hdr(10, 1, 1), _hdr(11, 1, 1)])
    assert units == [
        BoundaryUnit(start_page=10, end_page=10, expected_pages=1, header_count=1),
        BoundaryUnit(start_page=11, end_page=11, expected_pages=1, header_count=1),
    ]


def test_x_must_strictly_increase() -> None:
    """An LLM-noisy page that re-emits the previous X value
    (e.g. operator hit copy/paste error) doesn't extend the unit;
    treated as a new unit start (which requires X==1, so falls
    through and we discard the duplicate)."""

    units = group_boundary_units([_hdr(1, 1, 3), _hdr(2, 1, 3), _hdr(3, 3, 3)])
    # Pages 1, 2 — both at X=1 — close the first unit at page 1
    # (single-page), then page 2 starts a fresh unit, then page 3
    # (X=3) doesn't extend it (X must strictly increase, and the
    # current state-machine flushes on X reset). The exact shape:
    # unit at page 1, unit at page 2.
    assert len(units) == 2
    assert units[0].start_page == 1
    assert units[1].start_page == 2


def test_empty_input_returns_empty() -> None:
    assert group_boundary_units([]) == []


def test_low_confidence_headers_still_group() -> None:
    """Confidence is a HITL signal, not a grouping veto — typo'd
    headers still anchor units. ``header_count`` doesn't track
    confidence; the caller can compute that separately if it
    wants to weight."""

    units = group_boundary_units([
        _hdr(1, 1, 2, confidence=0.7),
        _hdr(2, 2, 2, confidence=0.7),
    ])
    assert units == [
        BoundaryUnit(start_page=1, end_page=2, expected_pages=2, header_count=2),
    ]
