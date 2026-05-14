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
async def test_bpcr_id_fallback_when_type_is_unknown() -> None:
    """The LLM occasionally emits BPCR sections with non-canonical
    types (``main_record``, ``production_record``, …) that
    ``normalize_section_types_to_canonical`` collapses to
    ``unknown``. Enrichment must STILL fire on those sections —
    via the ``section_id`` fallback in ``_is_bpcr_parent`` — so
    the BPCR body gets drilled rather than left as one opaque
    ``unknown`` block.
    """

    # LLM emits a section named bpcr_main_record but types it
    # vaguely. Pre-fix this got normalized to ``unknown`` BEFORE
    # enrichment ran, and enrichment refused to touch it.
    raw = DocumentSegmentation(
        sections=[
            _sec(
                "bpcr_main_record",
                1, 10,
                section_type="main_record",  # non-canonical
                document_type="batch_record",
            ),
        ],
        document_type="batch_record",
        confidence=0.9,
    )
    seg_llm = _StubLLM(raw)
    segmenter = DocumentSegmenter(seg_llm)
    # Synthesise markdown for the BPCR pages with the headings the
    # heuristic detector recognises. Page 1 has cover_page-like
    # title; page 2 has revision header; page 3 has the raw-
    # material heading.
    extractions = [
        {"page_num": 1, "markdown": "Batch Production and Control Record\n\nCover."},
        {"page_num": 2, "markdown": "**REVISION SUMMARY**\n"},
        {"page_num": 3, "markdown": "## **LIST OF RAW MATERIALS AND WEIGHING DETAILS**"},
        {"page_num": 4, "markdown": "(continued)"},
        {"page_num": 5, "markdown": "## **LIST OF MAJOR EQUIPMENTS & SOP DETAILS**"},
        {"page_num": 6, "markdown": "# MANUFACTURING INSTRUCTIONS"},
        {"page_num": 7, "markdown": "(continued)"},
        {"page_num": 8, "markdown": "(continued)"},
        {"page_num": 9, "markdown": "(continued)"},
        {"page_num": 10, "markdown": "(continued)"},
    ]

    result = await segmenter.segment(
        extractions=extractions,
        total_pages=10,
        filename="bpcr-id-fallback.pdf",
    )

    types = {s.section_type for s in result.sections}
    # The detector should at least find SOME canonical
    # sub-section types — proves enrichment fired despite the
    # vague LLM section_type.
    canonical_bpcr_subsections = {
        "cover_page", "revision_summary", "material_dispensing",
        "equipment_list", "manufacturing_operations",
    }
    assert types & canonical_bpcr_subsections, (
        f"enrichment didn't fire despite section_id signalling BPCR; "
        f"output section types: {types}"
    )


@pytest.mark.asyncio
async def test_enrichment_translates_relative_page_numbers_to_absolute() -> None:
    """The BPCR heuristic detector returns spans with page numbers
    1-indexed within its input, NOT absolute against the source
    document. Enrichment must translate those back to absolute so
    the detected spans land on the right pages.

    Akhilesh's 2026-05-14 doc surfaced this: ``bpcr_main_record``
    spanning pages 11-19 produced a flatten span at page 1-9 (the
    detector's relative numbering) which collided with the cover
    page section. After translation, spans land on pages 11-19.
    """

    raw = DocumentSegmentation(
        sections=[
            _sec(
                "bpcr",
                11, 15,
                section_type="batch_record",
                document_type="batch_record",
            ),
        ],
        document_type="batch_record",
        confidence=0.9,
    )
    seg_llm = _StubLLM(raw)
    segmenter = DocumentSegmenter(seg_llm)
    # Insert markdown with a detectable heading on page 12 (i.e.
    # relative page 2 within the BPCR's input).
    extractions = [
        {"page_num": p, "markdown": "filler"} for p in range(1, 11)
    ] + [
        {"page_num": 11, "markdown": "Batch cover"},
        {"page_num": 12, "markdown": "## **LIST OF RAW MATERIALS AND WEIGHING DETAILS**"},
        {"page_num": 13, "markdown": "(continued)"},
        {"page_num": 14, "markdown": "# MANUFACTURING INSTRUCTIONS"},
        {"page_num": 15, "markdown": "(continued)"},
    ]

    result = await segmenter.segment(
        extractions=extractions,
        total_pages=15,
        filename="page-offset.pdf",
    )

    # Find any enriched sub-section and assert it lands on
    # ABSOLUTE pages within the BPCR's 11-15 range, NOT on
    # relative 1-N.
    bpcr_subsections = [
        s for s in result.sections
        if s.section_id.startswith("bpcr__")
    ]
    assert bpcr_subsections, "enrichment didn't produce flatten spans"
    for s in bpcr_subsections:
        assert 11 <= s.start_page <= 15, (
            f"enriched span {s.section_id} at p{s.start_page}-{s.end_page} "
            f"escaped the parent range (11-15) — page-offset translation broken"
        )
        assert 11 <= s.end_page <= 15, (
            f"enriched span {s.section_id} at p{s.start_page}-{s.end_page} "
            f"escaped the parent range (11-15)"
        )


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
