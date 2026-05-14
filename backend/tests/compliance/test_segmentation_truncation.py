"""Tests for Spec 011 / US2 — output-truncation detection +
retry loop.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.compliance.models import DocumentSection, DocumentSegmentation
from app.compliance.segmentation import (
    _TRUNCATION_COVERAGE_THRESHOLD,
    _TRUNCATION_MAX_RETRIES,
    DocumentSegmenter,
    detect_truncation,
)


def _seg(*pairs: tuple[int, int]) -> DocumentSegmentation:
    sections = [
        DocumentSection(
            section_id=f"s{i}",
            name=f"section {i}",
            section_type="material_request",
            document_type="raw_material_request",
            start_page=lo,
            end_page=hi,
            description="",
        )
        for i, (lo, hi) in enumerate(pairs)
    ]
    return DocumentSegmentation(sections=sections, document_type="batch_record", confidence=0.9)


# ── detect_truncation ──────────────────────────────────────────


def test_full_coverage_returns_none() -> None:
    """A segmentation covering 100% of pages is NOT truncated."""

    seg = _seg((1, 100))
    assert detect_truncation(seg, total_pages=100) is None


def test_above_threshold_returns_none() -> None:
    """98% coverage (above 97%) is acceptable — fill_gaps_with_
    unknown handles the remainder downstream."""

    seg = _seg((1, 98))
    assert detect_truncation(seg, total_pages=100) is None


def test_below_threshold_returns_first_uncovered_page() -> None:
    """50% coverage triggers retry from page 51."""

    seg = _seg((1, 50))
    assert detect_truncation(seg, total_pages=100) == 51


def test_returns_first_gap_when_internal_holes_exist() -> None:
    """If the LLM emitted 1-30 and 60-80 (covers 50/100 pages),
    the retry anchor is page 31 — the FIRST uncovered, not the
    last."""

    seg = _seg((1, 30), (60, 80))
    assert detect_truncation(seg, total_pages=100) == 31


def test_zero_total_pages_returns_none() -> None:
    """Defensive: caller might not know total_pages."""

    assert detect_truncation(_seg((1, 10)), total_pages=0) is None


def test_threshold_constant_is_exposed() -> None:
    """Pin the threshold so a future change is a deliberate
    decision, not a silent regression."""

    assert _TRUNCATION_COVERAGE_THRESHOLD == 0.97
    assert _TRUNCATION_MAX_RETRIES == 2


# ── Retry loop in DocumentSegmenter ────────────────────────────


class _StubTruncatingLLM:
    """LLM that returns a truncated response on the first call and
    a complete tail on the second call (simulating a successful
    retry).

    We don't exercise the prompt content — only the call shape and
    response objects.
    """

    def __init__(self, first: DocumentSegmentation, tail: DocumentSegmentation) -> None:
        self._first = first
        self._tail = tail
        self._calls = 0

    async def generate(self, *_args: Any, **_kwargs: Any) -> str:
        raise NotImplementedError

    async def generate_structured(self, *_args: Any, **_kwargs: Any) -> DocumentSegmentation:
        self._calls += 1
        if self._calls == 1:
            return self._first
        return self._tail


class _StubAlwaysTruncatingLLM:
    """LLM that returns the same short coverage on every call —
    exercises the retry-exhausted path."""

    def __init__(self, seg: DocumentSegmentation) -> None:
        self._seg = seg
        self._calls = 0

    async def generate(self, *_args: Any, **_kwargs: Any) -> str:
        raise NotImplementedError

    async def generate_structured(self, *_args: Any, **_kwargs: Any) -> DocumentSegmentation:
        self._calls += 1
        return self._seg


@pytest.mark.asyncio
async def test_truncated_first_call_recovers_via_retry() -> None:
    """LLM truncates at page 100 of 200; second call covers
    101-200; final coverage is 1-200 with no truncation event
    persisting."""

    first = _seg((1, 100))
    tail = _seg((101, 200))
    seg_llm = _StubTruncatingLLM(first, tail)
    segmenter = DocumentSegmenter(seg_llm)

    # Synthesise extractions with absolute page numbers.
    extractions = [{"page_num": p, "markdown": f"page {p}"} for p in range(1, 201)]

    result = await segmenter.segment(
        extractions=extractions,
        total_pages=200,
        filename="long.pdf",
    )

    # Two LLM calls happened (first + one retry).
    assert seg_llm._calls == 2
    # Final coverage includes pages 1-200.
    covered: set[int] = set()
    for sec in result.sections:
        for p in range(sec.start_page, sec.end_page + 1):
            covered.add(p)
    assert covered == set(range(1, 201))


@pytest.mark.asyncio
async def test_retry_cap_is_two_attempts() -> None:
    """When every retry also truncates, the loop stops after 2
    attempts (3 total LLM calls); remaining range is filled with
    ``unknown`` by ``fill_gaps_with_unknown`` downstream."""

    stub = _StubAlwaysTruncatingLLM(_seg((1, 50)))
    segmenter = DocumentSegmenter(stub)
    extractions = [{"page_num": p, "markdown": ""} for p in range(1, 201)]

    await segmenter.segment(
        extractions=extractions,
        total_pages=200,
        filename="long.pdf",
    )

    # 1 initial + 2 retries = 3 calls.
    assert stub._calls == 1 + _TRUNCATION_MAX_RETRIES


@pytest.mark.asyncio
async def test_complete_first_call_skips_retry() -> None:
    """When the LLM covers ≥97% on the first call, no retry."""

    stub = _StubTruncatingLLM(_seg((1, 200)), _seg((201, 201)))
    segmenter = DocumentSegmenter(stub)
    extractions = [{"page_num": p, "markdown": ""} for p in range(1, 201)]

    await segmenter.segment(
        extractions=extractions,
        total_pages=200,
        filename="long.pdf",
    )

    assert stub._calls == 1
