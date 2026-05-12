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


def test_enrichment_flattens_bpcr_into_top_level_section_per_span() -> None:
    """End-to-end: a segmentation with a BPCR section + per-page
    markdown produces N top-level ``DocumentSection`` entries —
    one per canonical BPCR sub-section the detector recognized.

    This matches the gold-standard shape Akhilesh shared on
    2026-05-12: rather than ONE parent BPCR row with nested
    per-page rows, each sub-section gets its own top-level
    entry with a proper ``section_type`` + ``start_page`` /
    ``end_page``. Non-BPCR sections pass through unchanged.
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

    # The BPCR's one parent section is replaced by N top-level
    # entries — one per detected span. The non-BPCR section
    # passes through unchanged.
    bpcr_entries = [
        s for s in enriched.sections if s.document_type == "batch_record"
    ]
    rm_entries = [
        s for s in enriched.sections
        if s.section_type == "raw_material_request_and_issue"
    ]

    assert len(rm_entries) == 1, "non-BPCR section must pass through unchanged"
    assert rm_entries[0].sub_sections == []

    assert bpcr_entries, "BPCR enrichment produced no top-level entries"
    # All BPCR sub-section entries share the parent section_id.
    assert all(s.section_id == "bpcr_unit_ii" for s in bpcr_entries)
    # Each carries the right document_type for cross-doc filters.
    assert all(s.document_type == "batch_record" for s in bpcr_entries)

    # Pin the four mid-page markers and the cover page — these are
    # the headers the user's real BPCR carried. If these regress
    # the legacy compliance pipeline goes back to one opaque BPCR
    # block (Akhilesh's original symptom).
    by_type = {s.section_type: (s.start_page, s.end_page) for s in bpcr_entries}
    assert "cover_page" in by_type
    assert "revision_summary" in by_type
    assert "material_dispensing" in by_type
    assert "manufacturing_operations" in by_type
    assert "micronization" in by_type
    # Cover page is page 1; revision page 2; etc.
    assert by_type["cover_page"] == (1, 1)
    assert by_type["revision_summary"] == (2, 2)


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

    # Second pass produces the same section list — no re-flattening,
    # no duplicate entries. The first pass turned the parent BPCR
    # into N sub-section entries (each with section_type like
    # ``cover_page``, ``revision_summary``); the second pass sees
    # those entries as already-flattened (none of their
    # section_types match the BPCR hints) and passes them through
    # unchanged.
    once_shape = [
        (s.section_type, s.document_type, s.start_page, s.end_page)
        for s in once.sections
    ]
    twice_shape = [
        (s.section_type, s.document_type, s.start_page, s.end_page)
        for s in twice.sections
    ]
    assert once_shape == twice_shape, (
        "second pass changed the section list — enrichment is not idempotent"
    )
    # Sanity: the flattening actually happened on the first pass.
    assert len(once.sections) >= 1
    assert any(s.section_type == "cover_page" for s in once.sections)


def test_enrichment_flattens_llm_populated_sub_sections_when_detector_misses() -> None:
    """The 2026-05-12 segmentation prompt cues nudge the LLM to
    emit ``BpcrSubSection`` entries directly (often with
    ``detection_method='column_names'`` for cover_page /
    revision_summary, which have no section heading). Before this
    fix the flatten only consumed the heuristic detector's spans;
    LLM-populated sub_sections were dropped on the floor whenever
    the detector either returned nothing (parent kept whole) or
    returned its own spans (LLM entries discarded).

    Real-doc symptom from run e5e35ffc-…: BPCR parent arrived from
    the LLM with sub_sections=[cover_page, revision_summary] but
    the detector found nothing for those pages because there's no
    heading text on top of those tables, so the run came back with
    one opaque BPCR block.

    Pin: when the detector finds nothing, the LLM-populated
    sub_sections must still become top-level entries.
    """

    from app.compliance.models import BpcrSubSection

    seg = DocumentSegmentation(
        sections=[
            DocumentSection(
                section_id="bpcr",
                name="BPCR",
                section_type="batch_record",
                start_page=1, end_page=2,
                sub_sections=[
                    BpcrSubSection(
                        section_id="cover_page",
                        display_name="Cover Page",
                        page_index=1,
                        confidence=0.8,
                        detection_method="column_names",
                    ),
                    BpcrSubSection(
                        section_id="revision_summary",
                        display_name="Revision Summary",
                        page_index=2,
                        confidence=0.8,
                        detection_method="column_names",
                    ),
                ],
            ),
        ],
    )
    # Markdown deliberately bland — the heuristic detector has no
    # heading text to latch onto, so all flatten output must come
    # from the LLM-populated sub_sections.
    extractions = [
        {"page_num": 1, "markdown": "page 1 plain content"},
        {"page_num": 2, "markdown": "page 2 plain content"},
    ]

    enriched = enrich_with_bpcr_sub_sections(seg, extractions)

    by_type = {s.section_type: (s.start_page, s.end_page) for s in enriched.sections}
    assert "cover_page" in by_type, (
        "LLM-populated cover_page sub_section was dropped — flatten "
        "regression from PR #42 not yet fixed"
    )
    assert "revision_summary" in by_type
    assert by_type["cover_page"] == (1, 1)
    assert by_type["revision_summary"] == (2, 2)
    # Each carries the right document_type for cross-doc filters.
    for s in enriched.sections:
        assert s.document_type == "batch_record"
        assert s.section_id == "bpcr"  # parent section_id preserved


def test_enrichment_merges_detector_spans_with_llm_sub_sections() -> None:
    """When BOTH sources fire — detector finds heading-anchored
    sections (manufacturing_operations) AND the LLM populates
    sub_sections for the heading-less ones (cover_page) — the
    flatten must include all of them. Detector wins on
    section_type collisions because its spans carry real page
    ranges; LLM ``page_index`` is a single int."""

    from app.compliance.models import BpcrSubSection

    seg = DocumentSegmentation(
        sections=[
            DocumentSection(
                section_id="bpcr",
                name="BPCR",
                section_type="batch_record",
                start_page=1, end_page=4,
                sub_sections=[
                    # LLM only knows the heading-less ones.
                    BpcrSubSection(
                        section_id="cover_page",
                        display_name="Cover",
                        page_index=1,
                        confidence=0.8,
                        detection_method="column_names",
                    ),
                    BpcrSubSection(
                        section_id="revision_summary",
                        display_name="Revision",
                        page_index=2,
                        confidence=0.8,
                        detection_method="column_names",
                    ),
                ],
            ),
        ],
    )
    # Markdown carries headings for the heading-anchored sections
    # so the heuristic detector fires on pages 3-4. The flatten
    # must combine its spans with the LLM's pages 1-2 entries.
    extractions = [
        {"page_num": 1, "markdown": "Cover content with BPCR Number columns."},
        {"page_num": 2, "markdown": "Revision content with Change History columns."},
        {"page_num": 3, "markdown": "**MANUFACTURING INSTRUCTIONS**\nDate:"},
        {"page_num": 4, "markdown": "**MICRONIZATION OPERATION**\n| col |"},
    ]

    enriched = enrich_with_bpcr_sub_sections(seg, extractions)
    types = {s.section_type for s in enriched.sections}

    # LLM-only types still present.
    assert "cover_page" in types
    assert "revision_summary" in types
    # Detector-only types present too — the merge didn't crowd them out.
    assert "manufacturing_operations" in types or "micronization" in types
    # No duplicate top-level entries for the same section_type.
    seen: list[str] = [s.section_type for s in enriched.sections]
    assert len(seen) == len(set(seen)), (
        f"merged flatten produced duplicate section_types: {seen}"
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
