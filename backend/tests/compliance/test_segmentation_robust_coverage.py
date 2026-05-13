"""Pin the robust-coverage segmentation contract.

Akhilesh's 2026-05-13 audit on 2538105062.pdf surfaced two concrete
classes of failure the per-fix prompt cues hadn't closed:

1. **Misclassification on header look-alikes.** The In-Process
   Samples Request Cum Analysis Report was classified as
   ``analysis_report`` instead of ``ipc_report`` — the LLM saw
   "Analysis Report" in the header and skipped the "In-Process
   Samples" framing that document_profiles.yaml maps to ``ipc_report``.

2. **Coverage gaps the LLM silently leaves.** Pages 24, 56-58,
   72-76, 95-98 were not covered by any section. The compliance
   pipeline would never have evaluated rules against those pages
   — a regulator-visible blackout.

This module pins three robust resolutions:

* The prompt now carries a HARD COVERAGE CONSTRAINT block and an
  explicit ipc_report / analysis_report mapping the LLM has to
  follow.
* The validator emits gap events with adjacency context (what
  section is before / after the gap) and a "likely continuation"
  hint when both neighbours share section_type.
* ``fill_gaps_with_unknown`` is a deterministic post-process that
  replaces any LLM-left gap with an explicit
  ``section_type='unknown'`` section so pages are preserved AND
  flagged for HITL re-classification.
"""

from __future__ import annotations

import pytest

from app.compliance.models import DocumentSection, DocumentSegmentation
from app.compliance.segmentation import (
    _build_segmentation_prompt,
    fill_gaps_with_unknown,
    validate_segmentation,
)


# ── Prompt cues ─────────────────────────────────────────────────


def test_prompt_carries_hard_coverage_constraint() -> None:
    """The prompt MUST encode 'no gaps, no overlaps' as the load-
    bearing rule — Akhilesh's symptom on 2538105062.pdf was four
    page ranges (24, 56-58, 72-76, 95-98) silently uncovered."""

    prompt = _build_segmentation_prompt(
        extractions=[{"page_num": 1, "markdown": "test"}],
        key_value_pairs=None,
        filename="test.pdf",
    )
    assert "HARD COVERAGE" in prompt or "MUST be covered" in prompt
    assert "NO gaps" in prompt or "no gaps" in prompt.lower()
    assert "NO overlaps" in prompt or "no overlaps" in prompt.lower()
    # The fallback escape hatch — unclassifiable pages become
    # section_type='unknown' rather than getting dropped.
    assert "unknown" in prompt.lower()


def test_prompt_disambiguates_ipc_report_from_analysis_report() -> None:
    """The header 'In-Process Samples Request Cum Analysis Report'
    must route to ipc_report — not analysis_report. The prompt has
    to call this out explicitly because the LLM otherwise latches
    on the trailing 'Analysis Report' substring."""

    prompt = _build_segmentation_prompt(
        extractions=[{"page_num": 1, "markdown": "test"}],
        key_value_pairs=None,
        filename="test.pdf",
    )
    # Explicit IPC routing must be present.
    assert "ipc_report" in prompt
    assert "in-process samples" in prompt.lower()
    # The anti-pattern must be called out by name.
    assert "NEVER classify" in prompt or "never classify" in prompt.lower()


def test_prompt_carries_operation_checklist_boundary_rule() -> None:
    """Each operation_checklist starts with its own
    'Check List for X Operations' header. The prompt must direct
    the LLM to start a new section on that header — Akhilesh's
    p55 was glued to the prior batch_release_note because this
    boundary rule wasn't in the prompt."""

    prompt = _build_segmentation_prompt(
        extractions=[{"page_num": 1, "markdown": "test"}],
        key_value_pairs=None,
        filename="test.pdf",
    )
    assert "Check List for" in prompt
    assert "operation_checklist" in prompt
    # The "new section starts" anchor must be explicit.
    assert "NEW section" in prompt or "new section" in prompt.lower()


def test_prompt_calls_out_scada_data_vs_alarm_clusters() -> None:
    """VDE0** data report + alarm report are SEPARATE sections with
    the same document_type=scada_report. The prompt has to direct
    the LLM not to merge them."""

    prompt = _build_segmentation_prompt(
        extractions=[{"page_num": 1, "markdown": "test"}],
        key_value_pairs=None,
        filename="test.pdf",
    )
    assert "alarm" in prompt.lower()
    assert "scada_report" in prompt
    # Separate-sections cue must be present (line-wrapping tolerant).
    flat = " ".join(prompt.split())
    assert "SEPARATE sections" in flat or "separate sections" in flat.lower()


# ── Validator: gap with adjacency context ──────────────────────


def test_gap_event_includes_adjacent_section_context() -> None:
    """A gap between two sections must surface the section_id /
    section_type of the BEFORE and AFTER neighbours in the issue
    message so the operator sees the neighbourhood (and can decide
    whether to extend a neighbour vs. add a new section)."""

    seg = DocumentSegmentation(
        sections=[
            DocumentSection(
                section_id="reactor_chklst",
                name="Reactor Checklist",
                section_type="reactor_checklist",
                document_type="operation_checklist",
                start_page=1, end_page=5,
            ),
            DocumentSection(
                section_id="vacuum_chklst",
                name="Vacuum Dryer Checklist",
                section_type="vacuum_tray_dryer_checklist",
                document_type="operation_checklist",
                start_page=8, end_page=10,
            ),
        ],
    )
    issues = validate_segmentation(seg, total_pages=10)
    gaps = [i for i in issues if i.kind == "gap"]
    assert gaps, "validator must flag pages 6-7 as a gap"
    g = gaps[0]
    assert g.page_range == (6, 7)
    # Both neighbours surfaced.
    assert "reactor_chklst" in g.message
    assert "vacuum_chklst" in g.message
    # Adjacency context preserved in section_ids tuple too.
    assert set(g.section_ids) == {"reactor_chklst", "vacuum_chklst"}


def test_gap_event_suggests_continuation_when_neighbours_share_type() -> None:
    """When the sections immediately before and after a gap share
    section_type, the gap is overwhelmingly a continuation the LLM
    split incorrectly. The validator must call this out so the
    operator's first instinct is to extend rather than introduce
    a third entity."""

    seg = DocumentSegmentation(
        sections=[
            DocumentSection(
                section_id="man_ops_a",
                name="Manufacturing Operations Part A",
                section_type="manufacturing_operations",
                document_type="batch_record",
                start_page=1, end_page=5,
            ),
            DocumentSection(
                section_id="man_ops_b",
                name="Manufacturing Operations Part B",
                section_type="manufacturing_operations",
                document_type="batch_record",
                start_page=9, end_page=12,
            ),
        ],
    )
    issues = validate_segmentation(seg, total_pages=12)
    gap = next(i for i in issues if i.kind == "gap")
    assert "continuation" in gap.message.lower() or \
           "extending" in gap.message.lower(), (
        "gap between two same-type sections must hint at extension"
    )


# ── Deterministic gap-fill ─────────────────────────────────────


def test_fill_gaps_with_unknown_closes_llm_left_gaps() -> None:
    """Pages the LLM left uncovered must become explicit
    section_type='unknown' entries so compliance has a chance to
    look at them AND so HITL reviewers see the gap was real."""

    seg = DocumentSegmentation(
        sections=[
            DocumentSection(
                section_id="part_a", name="A",
                section_type="manufacturing_operations",
                document_type="batch_record",
                start_page=1, end_page=10,
            ),
            DocumentSection(
                section_id="part_b", name="B",
                section_type="raw_material_request_and_issue",
                document_type="raw_material_request",
                start_page=14, end_page=20,
            ),
        ],
    )
    filled = fill_gaps_with_unknown(seg, total_pages=20)
    unknown_secs = [s for s in filled.sections if s.section_type == "unknown"]
    assert len(unknown_secs) == 1
    u = unknown_secs[0]
    assert (u.start_page, u.end_page) == (11, 13)
    assert u.document_type == "", "unknown sections must NOT claim a doc_type"


def test_fill_gaps_with_unknown_is_idempotent() -> None:
    """The fill must be safe to run on a segmentation that already
    has unknown sections — running twice produces the same shape."""

    seg = DocumentSegmentation(
        sections=[
            DocumentSection(
                section_id="part_a", name="A",
                section_type="manufacturing_operations",
                document_type="batch_record",
                start_page=1, end_page=5,
            ),
        ],
    )
    once = fill_gaps_with_unknown(seg, total_pages=10)
    twice = fill_gaps_with_unknown(once, total_pages=10)
    once_shape = [(s.start_page, s.end_page, s.section_type) for s in once.sections]
    twice_shape = [(s.start_page, s.end_page, s.section_type) for s in twice.sections]
    assert once_shape == twice_shape


def test_fill_gaps_with_unknown_handles_multiple_disjoint_gaps() -> None:
    """The 2538105062.pdf audit found four disjoint gaps (24,
    56-58, 72-76, 95-98). Each must become its own unknown
    section — the fill must NOT coalesce them into one giant
    pseudo-section that spans real content."""

    seg = DocumentSegmentation(
        sections=[
            DocumentSection(section_id="a", name="A", section_type="x",
                            start_page=1, end_page=23),
            # gap: 24
            DocumentSection(section_id="b", name="B", section_type="x",
                            start_page=25, end_page=55),
            # gap: 56-58
            DocumentSection(section_id="c", name="C", section_type="x",
                            start_page=59, end_page=71),
            # gap: 72-76
            DocumentSection(section_id="d", name="D", section_type="x",
                            start_page=77, end_page=94),
            # gap: 95-98
            DocumentSection(section_id="e", name="E", section_type="x",
                            start_page=99, end_page=109),
        ],
    )
    filled = fill_gaps_with_unknown(seg, total_pages=109)
    unknown_ranges = sorted(
        (s.start_page, s.end_page)
        for s in filled.sections if s.section_type == "unknown"
    )
    assert unknown_ranges == [(24, 24), (56, 58), (72, 76), (95, 98)]


def test_fill_gaps_with_unknown_preserves_order() -> None:
    """The filled sections must come back page-ordered so the
    frontend's chronological view doesn't break."""

    seg = DocumentSegmentation(
        sections=[
            DocumentSection(section_id="b", name="B", section_type="x",
                            start_page=5, end_page=8),
            DocumentSection(section_id="a", name="A", section_type="x",
                            start_page=1, end_page=2),
        ],
    )
    filled = fill_gaps_with_unknown(seg, total_pages=10)
    page_order = [(s.start_page, s.end_page) for s in filled.sections]
    assert page_order == sorted(page_order)
