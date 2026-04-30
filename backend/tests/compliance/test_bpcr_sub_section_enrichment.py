"""Compliance pipeline now drills BPCR sections into 13 sub-sections.

Akhilesh's 2026-04-28 ask was specifically: "still not returning the
sections within batch_record". PR #9 wired BPCR sub-section detection
into the new BMR pipeline (``/api/bmr/runs``); this module wires the
same pure capability into the **legacy compliance pipeline** that
real users hit at ``/api/compliance/{doc_id}/run``.

The enrichment is a post-processing step over LLM segmentation output.
Pure pass-through for non-BPCR sections; for any section whose
``section_type`` matches the BPCR hints, runs the heuristic detector
over the per-page markdown and populates ``sub_sections`` with one
entry per page covered by the detector's spans.
"""

from __future__ import annotations

import pytest

from app.compliance.models import (
    DocumentSection,
    DocumentSegmentation,
)
from app.compliance.segmentation import (
    _looks_like_bpcr,
    enrich_with_bpcr_sub_sections,
)


@pytest.mark.parametrize("section_type,expected", [
    ("batch_record", True),
    ("batch_production_and_control_record", True),
    ("batch_production_record", True),
    ("bpcr", True),
    ("batch_production_and_control_record_unit_ii", True),  # real LLM output
    # Look-alikes that must NOT trigger — different documents:
    ("batch_packaging_record", False),
    ("batch_release_record", False),
    ("raw_material_request_and_issue", False),
    ("certificate_of_analysis", False),
    ("", False),
])
def test_bpcr_hint_matcher(section_type: str, expected: bool) -> None:
    """The hint-set is the safety rail — running the BPCR detector on
    a non-BPCR document would emit nonsense sub-sections that survive
    in the report. Pin the inclusion / exclusion set explicitly."""

    assert _looks_like_bpcr(section_type) is expected


def test_enrichment_passes_non_bpcr_sections_through_unchanged() -> None:
    """A document with no BPCR section must round-trip with no changes
    — the enrichment is supposed to be a pure post-process."""

    seg = DocumentSegmentation(
        document_type="raw_material_request",
        confidence=0.9,
        sections=[
            DocumentSection(
                section_id="rm_req",
                name="Raw Material Request",
                section_type="raw_material_request_and_issue",
                start_page=1, end_page=10,
            ),
        ],
    )
    extractions = [
        {"page_num": p, "markdown": f"page {p} content"} for p in range(1, 11)
    ]

    enriched = enrich_with_bpcr_sub_sections(seg, extractions)

    assert len(enriched.sections) == 1
    assert enriched.sections[0].sub_sections == []
    # Original segmentation is not mutated.
    assert seg.sections[0].sub_sections == []


def test_enrichment_drills_bpcr_section_into_sub_sections() -> None:
    """End-to-end: a segmentation with a BPCR section + per-page
    markdown produces ``sub_sections`` for that BPCR. Pages outside
    the BPCR's page range are not touched.
    """

    seg = DocumentSegmentation(
        document_type="pharmaceutical_batch_production_and_quality_control_record",
        confidence=0.95,
        sections=[
            DocumentSection(
                section_id="bpcr_unit_ii",
                name="Batch Production and Control Record - Unit II",
                section_type="batch_production_and_control_record_unit_ii",
                start_page=1, end_page=5,
            ),
            DocumentSection(
                section_id="rm_req",
                name="Raw Material Request",
                section_type="raw_material_request_and_issue",
                start_page=6, end_page=10,
            ),
        ],
    )

    # Mirror the real-doc layout: page 1 cover, page 2 revision,
    # page 3 dispensing list, page 4 manufacturing instructions,
    # page 5 micronization. Plus a non-BPCR raw-material section
    # at pages 6-10 to confirm pass-through.
    extractions = [
        {"page_num": 1, "markdown": "Batch Production and Control Record\n\nCover content."},
        {"page_num": 2, "markdown": "Page 2 header\n\n**REVISION SUMMARY**\n| change |"},
        {"page_num": 3, "markdown": "Page 3\n\n**LIST OF RAW MATERIALS AND WEIGHING DETAILS**\n| material |"},
        {"page_num": 4, "markdown": "Page 4\n\n**MANUFACTURING INSTRUCTIONS**\nDate:"},
        {"page_num": 5, "markdown": "Page 5\n\n**MICRONIZATION OPERATION**\n| col |"},
        # Non-BPCR pages — must not be touched.
        {"page_num": 6, "markdown": "Raw Material Request page 6"},
        {"page_num": 10, "markdown": "Raw Material Request page 10"},
    ]

    enriched = enrich_with_bpcr_sub_sections(seg, extractions)

    bpcr = next(s for s in enriched.sections if "batch_production" in s.section_type)
    rm = next(s for s in enriched.sections if s.section_type == "raw_material_request_and_issue")

    # Non-BPCR section is unchanged.
    assert rm.sub_sections == []

    # BPCR section has at least one entry per page in its range,
    # mapping back to a known section_id from the spec.
    assert bpcr.sub_sections, "BPCR enrichment produced no sub_sections"
    by_page = {ss.page_index: ss.section_id for ss in bpcr.sub_sections}

    # Pin the four mid-page markers and the cover page — these are
    # the headers the user's real BPCR carried. If these regress
    # the legacy compliance pipeline goes back to one opaque BPCR
    # block (Akhilesh's original symptom).
    assert by_page.get(1) == "cover_page"
    assert by_page.get(2) == "revision_summary"
    assert by_page.get(3) == "material_dispensing"
    assert by_page.get(4) == "manufacturing_operations"
    assert by_page.get(5) == "micronization"


def test_enrichment_is_idempotent_so_cache_upgrade_is_safe() -> None:
    """The compliance graph runs enrichment on cached segmentations
    that lack ``sub_sections`` (i.e. cached before this feature
    shipped). Pinning idempotency here protects that upgrade path —
    re-running on an already-enriched seg must produce equivalent
    output, not duplicate or shift entries.

    The user's symptom was the failure mode this prevents: a
    pre-existing ``segmentation.json`` from before PR #22 caused
    ``load_segmentation`` to return non-None, the original code
    skipped the enrichment block, and the BPCR section came back
    with empty ``sub_sections`` even on a fresh compliance run.
    """

    seg = DocumentSegmentation(
        sections=[
            DocumentSection(
                section_id="bpcr",
                name="BPCR",
                section_type="batch_record",
                start_page=1, end_page=2,
            ),
        ],
    )
    extractions = [
        {"page_num": 1, "markdown": "Batch Production and Control Record\n\nCover."},
        {"page_num": 2, "markdown": "**REVISION SUMMARY**\n| change |"},
    ]

    once = enrich_with_bpcr_sub_sections(seg, extractions)
    twice = enrich_with_bpcr_sub_sections(once, extractions)

    # Same row count, same shape — second pass doesn't compound.
    assert len(twice.sections[0].sub_sections) == len(once.sections[0].sub_sections)
    assert (
        [(ss.section_id, ss.page_index) for ss in twice.sections[0].sub_sections]
        == [(ss.section_id, ss.page_index) for ss in once.sections[0].sub_sections]
    )


def test_enrichment_fails_open_when_no_markdown_for_bpcr_pages() -> None:
    """If the extractions lack markdown for the BPCR's page range
    (rare but possible — a doc with image-only pages), the section
    passes through with empty ``sub_sections`` rather than crashing
    or fabricating spans."""

    seg = DocumentSegmentation(
        sections=[
            DocumentSection(
                section_id="bpcr",
                name="BPCR",
                section_type="batch_record",
                start_page=1, end_page=5,
            ),
        ],
    )
    # No markdown — empty strings.
    extractions = [{"page_num": p, "markdown": ""} for p in range(1, 6)]

    enriched = enrich_with_bpcr_sub_sections(seg, extractions)

    assert enriched.sections[0].sub_sections == []
