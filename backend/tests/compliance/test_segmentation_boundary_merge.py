"""Tests for Spec 011 / US1 — boundary-aware merge and split of LLM
segmentation sections against page-header attested boundary units.
"""

from __future__ import annotations

from app.compliance.models import DocumentSection, DocumentSegmentation
from app.compliance.segmentation import merge_split_by_boundary
from app.compliance.segmentation_headers import BoundaryUnit


def _sec(
    section_id: str,
    start: int,
    end: int,
    *,
    section_type: str = "material_request",
    document_type: str = "raw_material_request",
    name: str = "",
) -> DocumentSection:
    return DocumentSection(
        section_id=section_id,
        name=name or section_id.replace("_", " ").title(),
        section_type=section_type,
        document_type=document_type,
        start_page=start,
        end_page=end,
        description="",
    )


def _seg(*sections: DocumentSection) -> DocumentSegmentation:
    return DocumentSegmentation(
        sections=list(sections),
        document_type="batch_record",
        confidence=0.9,
    )


def _unit(start: int, end: int, *, expected_pages: int | None = None) -> BoundaryUnit:
    return BoundaryUnit(
        start_page=start,
        end_page=end,
        expected_pages=expected_pages or (end - start + 1),
        header_count=end - start + 1,
    )


# ── Merge ──────────────────────────────────────────────────────


def test_merges_split_sections_inside_one_boundary_unit() -> None:
    """The canonical US1 case: the LLM split a 3-page form into
    two sections; the boundary merger collapses them to one whose
    page range matches the unit's."""

    seg = _seg(
        _sec("rm_req_pt1", 30, 31, section_type="material_request"),
        _sec("rm_iss_pt2", 32, 32, section_type="material_issue"),
    )
    units = [_unit(30, 32)]

    out = merge_split_by_boundary(seg, units)

    assert len(out.sections) == 1
    merged = out.sections[0]
    assert (merged.start_page, merged.end_page) == (30, 32)
    # rm_req_pt1 covers 2 pages, rm_iss_pt2 covers 1 — the longer
    # one wins.
    assert merged.section_id == "rm_req_pt1"
    assert merged.section_type == "material_request"


def test_tie_break_lowest_start_page_wins() -> None:
    """When two LLM constituents cover the same number of pages,
    the one starting earlier wins — deterministic so the same
    LLM output produces the same merge result every run."""

    seg = _seg(
        _sec("first", 30, 31, section_type="material_request"),
        _sec("second", 32, 33, section_type="material_issue"),
    )
    units = [_unit(30, 33)]
    out = merge_split_by_boundary(seg, units)
    assert out.sections[0].section_id == "first"


def test_does_not_merge_outside_units() -> None:
    """LLM sections that don't fall inside any boundary unit pass
    through untouched — the header signal hasn't attested to
    anything for those pages."""

    seg = _seg(
        _sec("a", 1, 5, section_type="manufacturing_operations"),
        _sec("b", 6, 10, section_type="cleaning_log"),
    )
    units: list[BoundaryUnit] = []
    out = merge_split_by_boundary(seg, units)
    assert [(s.section_id, s.start_page, s.end_page) for s in out.sections] == [
        ("a", 1, 5),
        ("b", 6, 10),
    ]


# ── Split ──────────────────────────────────────────────────────


def test_splits_section_that_crosses_a_unit_transition() -> None:
    """One LLM section accidentally glues two adjacent forms
    together (pages 30-34 covering both a 3-page form and a 2-page
    form). The merger splits at the unit transition."""

    seg = _seg(_sec("glued", 30, 34, section_type="material_request"))
    units = [_unit(30, 32), _unit(33, 34)]

    out = merge_split_by_boundary(seg, units)

    ranges = [(s.start_page, s.end_page) for s in out.sections]
    assert (30, 32) in ranges
    assert (33, 34) in ranges
    assert len(ranges) == 2


def test_section_extending_past_a_unit_splits_at_unit_end() -> None:
    """LLM section spans 30-40; unit covers 30-32. The first
    piece (30-32) sits inside the unit; the trailing piece
    (33-40) sits outside it."""

    seg = _seg(_sec("over", 30, 40, section_type="material_request"))
    units = [_unit(30, 32)]

    out = merge_split_by_boundary(seg, units)
    ranges = sorted((s.start_page, s.end_page) for s in out.sections)
    assert ranges == [(30, 32), (33, 40)]


# ── No-op cases ────────────────────────────────────────────────


def test_idempotent_when_section_already_matches_unit() -> None:
    """An LLM section that already coincides with a boundary unit
    is unchanged on the first pass and on every subsequent pass."""

    seg = _seg(_sec("rm", 30, 32, section_type="material_request"))
    units = [_unit(30, 32)]

    once = merge_split_by_boundary(seg, units)
    twice = merge_split_by_boundary(once, units)

    assert once.model_dump() == twice.model_dump()
    assert [(s.start_page, s.end_page) for s in once.sections] == [(30, 32)]


def test_empty_units_passes_through() -> None:
    """Without any header-attested boundary units, the merger is
    a no-op."""

    seg = _seg(_sec("a", 1, 5))
    out = merge_split_by_boundary(seg, [])
    assert out.model_dump() == seg.model_dump()


def test_split_then_merge_composes_correctly() -> None:
    """An LLM section spanning two units AND a second section
    inside the second unit: the spanning section splits at the
    transition; the trailing piece then merges with the second
    LLM section because they share the same unit."""

    seg = _seg(
        _sec("spanner", 30, 34, section_type="material_request"),
        _sec("inner", 33, 34, section_type="material_issue"),
    )
    units = [_unit(30, 32), _unit(33, 34)]

    out = merge_split_by_boundary(seg, units)

    ranges = sorted((s.start_page, s.end_page) for s in out.sections)
    # Phase A splits spanner → (30-32) + (33-34).
    # Phase B merges the (33-34) split-piece with "inner" because
    # they share unit 2; spanner's 33-34 piece has 2 pages while
    # inner also has 2 pages — tie-break: lowest start_page wins.
    # Both start at 33, so the iteration order resolves it (the
    # spanner's piece was added first in Phase A).
    assert ranges == [(30, 32), (33, 34)]
