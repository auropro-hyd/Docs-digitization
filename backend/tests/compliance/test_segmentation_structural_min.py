"""Tests for Spec 011 / US2 — structural-minimums validator.

A ``batch_record`` profile declares ``cover_page`` as
``required: true``. If the LLM omits it, the validator emits a
``missing_required_section`` issue so HITL knows to act.
"""

from __future__ import annotations

from app.compliance.models import DocumentSection, DocumentSegmentation
from app.compliance.segmentation import (
    validate_segmentation,
    validate_structural_minimums,
)


def _sec(
    section_type: str,
    document_type: str,
    *,
    start: int = 1,
    end: int = 1,
    section_id: str | None = None,
) -> DocumentSection:
    return DocumentSection(
        section_id=section_id or f"{document_type}_{section_type}",
        name=section_type.replace("_", " ").title(),
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


def test_batch_record_missing_cover_page_emits_issue() -> None:
    """``cover_page`` is required on the ``batch_record`` profile.
    Omitting it must surface as a validation issue."""

    seg = _seg(
        _sec("manufacturing_operations", "batch_record", start=1, end=10),
        _sec("material_dispensing", "batch_record", start=11, end=15),
    )
    issues = validate_structural_minimums(seg)
    kinds = {(i.kind, i.message) for i in issues}
    assert any(
        kind == "missing_required_section" and "cover_page" in msg
        for kind, msg in kinds
    )


def test_complete_batch_record_emits_no_missing_required() -> None:
    """When every ``required: true`` section is emitted, the
    validator returns no issues for that profile."""

    seg = _seg(
        _sec("cover_page", "batch_record", start=1, end=1),
        _sec("material_dispensing", "batch_record", start=2, end=3),
        _sec("manufacturing_operations", "batch_record", start=4, end=20),
    )
    issues = validate_structural_minimums(seg)
    # The batch_record profile's required sections are cover_page,
    # material_dispensing, manufacturing_operations. All emitted —
    # no issues from THIS validator.
    assert all(i.kind != "missing_required_section" for i in issues), issues


def test_doc_type_without_required_sections_emits_nothing() -> None:
    """An ``ipc_report`` profile has no ``required: true``
    sections (whole-doc-as-section pattern). The validator must
    not emit phantom issues for it."""

    seg = _seg(_sec("in_process_report", "ipc_report", start=113, end=115))
    issues = validate_structural_minimums(seg)
    assert issues == []


def test_unknown_doc_type_is_silent() -> None:
    """An LLM-emitted doc_type not in profiles is already flagged
    by ``validate_segmentation``'s drift check; the structural
    minimum validator stays silent so we don't double-report."""

    seg = _seg(_sec("mystery", "made_up_doc_type", start=1, end=5))
    issues = validate_structural_minimums(seg)
    assert issues == []


def test_validate_segmentation_includes_missing_required() -> None:
    """The structural-minimum validator is hooked into the
    public ``validate_segmentation`` so its output flows through
    the same telemetry path as the other validators."""

    seg = _seg(_sec("manufacturing_operations", "batch_record", start=1, end=10))
    issues = validate_segmentation(seg, total_pages=10)
    kinds = {i.kind for i in issues}
    assert "missing_required_section" in kinds
