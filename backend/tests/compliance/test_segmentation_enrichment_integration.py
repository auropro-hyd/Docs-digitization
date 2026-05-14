"""Pin the architectural contract that enrichment runs INSIDE
``DocumentSegmenter.segment()``.

Akhilesh's 2026-05-14 run on ``2538105062.pdf`` exposed a class of
silent bugs where ``enrich_with_bpcr_sub_sections`` (called from
``compliance_graph.py`` AFTER ``segment()`` returned) emitted
overlapping page ranges and coverage gaps that the Spec 011
geometric post-processes had already cleaned up. The persisted
output had three overlapping BPCR sections (1-3 / 1-10 / 1-19)
and 13 uncovered pages, because nothing re-sanitized the enriched
output.

This module pins the architectural fix: enrichment is now part
of the segment() pipeline, followed by another round of
clamp / resolve_overlaps / fill_gaps so the persisted output
is geometrically clean by construction.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.compliance.models import DocumentSection, DocumentSegmentation
from app.compliance.segmentation import DocumentSegmenter


class _StubLLM:
    """Returns a pre-baked LLM segmentation."""

    def __init__(self, seg: DocumentSegmentation) -> None:
        self._seg = seg

    async def generate(self, *_args: Any, **_kwargs: Any) -> str:  # pragma: no cover
        raise NotImplementedError

    async def generate_structured(self, *_args: Any, **_kwargs: Any) -> DocumentSegmentation:
        return self._seg


def _sec(
    section_id: str,
    start: int,
    end: int,
    *,
    section_type: str = "batch_record",
    document_type: str = "batch_record",
) -> DocumentSection:
    return DocumentSection(
        section_id=section_id,
        name=section_id.replace("_", " ").title(),
        section_type=section_type,
        document_type=document_type,
        start_page=start,
        end_page=end,
        description="",
    )


@pytest.mark.asyncio
async def test_overlapping_bpcr_sections_get_sanitized_in_segment() -> None:
    """The exact pathology from Akhilesh's 2026-05-14 doc: LLM
    emits three overlapping BPCR sections; segment() must hand
    back disjoint sections after enrichment + sanitization, with
    every page covered."""

    raw = DocumentSegmentation(
        sections=[
            _sec("bpcr_review_checklist", 1, 3, section_type="batch_record"),
            _sec("bpcr_cover_page", 1, 10, section_type="batch_record"),
            _sec("bpcr_main_record", 1, 19, section_type="batch_record"),
        ],
        document_type="batch_record",
        confidence=0.9,
    )
    seg_llm = _StubLLM(raw)
    segmenter = DocumentSegmenter(seg_llm)

    # Synthesise extractions for 20 pages with minimal markdown so
    # the BPCR heuristic detector can't add hallucinated spans —
    # we're testing the geometric sanitization, not the detector.
    extractions = [{"page_num": p, "markdown": ""} for p in range(1, 21)]

    result = await segmenter.segment(
        extractions=extractions,
        total_pages=20,
        filename="overlap-fixture.pdf",
    )

    # Disjointness: walking sorted sections, no two overlap.
    sorted_secs = sorted(result.sections, key=lambda s: (s.start_page, s.end_page))
    for prev, cur in zip(sorted_secs, sorted_secs[1:]):
        assert cur.start_page > prev.end_page, (
            f"overlap left after segment() sanitization: "
            f"{prev.section_id} ({prev.start_page}-{prev.end_page}) and "
            f"{cur.section_id} ({cur.start_page}-{cur.end_page})"
        )

    # Coverage: every page from 1 to total_pages is in some section.
    covered: set[int] = set()
    for s in result.sections:
        for p in range(s.start_page, s.end_page + 1):
            covered.add(p)
    assert covered == set(range(1, 21)), (
        f"pages missing from coverage: {sorted(set(range(1, 21)) - covered)}"
    )


@pytest.mark.asyncio
async def test_enrichment_produces_unique_section_ids() -> None:
    """When enrichment flattens a BPCR parent into multiple sub-
    section spans, each gets a unique section_id so the HITL
    overrides sidecar can target them individually. Pre-fix,
    every span inherited the parent's id verbatim, which made
    overrides collide."""

    raw = DocumentSegmentation(
        sections=[
            _sec("bpcr", 1, 5, section_type="batch_record"),
        ],
        document_type="batch_record",
        confidence=0.9,
    )
    seg_llm = _StubLLM(raw)
    segmenter = DocumentSegmenter(seg_llm)
    extractions = [
        # Light markdown to give the detector something to potentially match.
        # The point isn't perfect detection — the point is that whatever
        # the detector + LLM produce, IDs are unique.
        {"page_num": p, "markdown": f"page {p}"} for p in range(1, 6)
    ]

    result = await segmenter.segment(
        extractions=extractions,
        total_pages=5,
        filename="unique-ids.pdf",
    )

    ids = [s.section_id for s in result.sections]
    assert len(ids) == len(set(ids)), (
        f"non-unique section_ids in segment() output: "
        f"{[x for x in ids if ids.count(x) > 1]}"
    )


@pytest.mark.asyncio
async def test_llm_emitted_duplicate_ids_get_disambiguated() -> None:
    """The LLM frequently emits duplicate ``section_id`` values
    for distinct raw-material forms (e.g. two pages both called
    ``raw_material_request_allocated``). The pipeline must
    disambiguate them so the US4 overrides sidecar can target
    each individually."""

    raw = DocumentSegmentation(
        sections=[
            _sec("rm_form", 10, 12, section_type="material_request",
                 document_type="raw_material_request"),
            _sec("rm_form", 13, 15, section_type="material_request",
                 document_type="raw_material_request"),
            _sec("rm_form", 16, 18, section_type="material_request",
                 document_type="raw_material_request"),
        ],
        document_type="raw_material_request",
        confidence=0.9,
    )
    seg_llm = _StubLLM(raw)
    segmenter = DocumentSegmenter(seg_llm)
    extractions = [{"page_num": p, "markdown": ""} for p in range(1, 21)]

    result = await segmenter.segment(
        extractions=extractions,
        total_pages=20,
        filename="dup-ids.pdf",
    )

    rm_sections = [s for s in result.sections if "rm_form" in s.section_id]
    assert len(rm_sections) == 3, "duplicate IDs were collapsed instead of disambiguated"
    ids = sorted(s.section_id for s in rm_sections)
    assert ids[0] == "rm_form"
    assert ids[1] == "rm_form_2"
    assert ids[2] == "rm_form_3"


@pytest.mark.asyncio
async def test_segment_validates_post_enrichment_state() -> None:
    """validation_issues must reflect the FINAL persisted state —
    not the pre-enrichment intermediate. Pre-fix, validators saw
    the pre-enrichment seg (where BPCR review checklist was at
    p115-117) but the persisted state had different page numbers,
    producing stale issue messages."""

    raw = DocumentSegmentation(
        sections=[
            _sec("bpcr", 1, 19, section_type="batch_record"),
        ],
        document_type="batch_record",
        confidence=0.9,
    )
    seg_llm = _StubLLM(raw)
    segmenter = DocumentSegmenter(seg_llm)
    extractions = [{"page_num": p, "markdown": ""} for p in range(1, 21)]

    result = await segmenter.segment(
        extractions=extractions,
        total_pages=20,
        filename="state-consistency.pdf",
    )

    # The validation_issues, if any, must reference page ranges
    # that map to sections that actually exist in the output.
    section_ids_in_output = {s.section_id for s in result.sections}
    for issue in result.validation_issues:
        for section_id in issue.get("section_ids") or []:
            assert section_id in section_ids_in_output, (
                f"validation_issue references section_id "
                f"{section_id!r} not present in the persisted "
                f"segmentation — validators ran on a stale view"
            )
