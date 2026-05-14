"""Tests for Spec 011 / US3 — cross-evidence validators.

Two validators that surface segmentation quality without mutating
the output:

* ``validate_kv_coverage`` — sections spanning ≥3 pages with zero
  OCR KV pairs in range are suspicious.
* ``validate_type_consistency`` — section_type that's not part of
  its declared document_type's profile is a contradiction.
"""

from __future__ import annotations

from app.compliance.models import DocumentSection, DocumentSegmentation
from app.compliance.segmentation import (
    validate_kv_coverage,
    validate_segmentation,
    validate_type_consistency,
)


def _sec(
    section_id: str,
    start: int,
    end: int,
    *,
    section_type: str,
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


def _seg(*sections: DocumentSection) -> DocumentSegmentation:
    return DocumentSegmentation(sections=list(sections), document_type="batch_record", confidence=0.9)


# ── KV-pair coverage ──────────────────────────────────────────


def test_kv_coverage_fires_for_long_span_with_no_kv() -> None:
    """A 6-page section with zero KV pairs in range surfaces a
    ``no_kv_evidence`` warning so HITL knows the LLM probably
    mis-classified an image-only block."""

    seg = _seg(_sec("rogue", 50, 55, section_type="manufacturing_operations"))
    kv = [
        {"page_num": 1, "key": "Batch No", "value": "X"},
        {"page_num": 100, "key": "Product", "value": "Y"},
    ]
    issues = validate_kv_coverage(seg, kv)
    assert any(i.kind == "no_kv_evidence" for i in issues)
    msg = issues[0].message
    assert "rogue" in msg and "50-55" in msg


def test_kv_coverage_silent_for_short_spans() -> None:
    """One- and two-page sections legitimately have zero KV pairs
    (single-page checklists, cover pages) — must not false-fire."""

    seg = _seg(
        _sec("one", 1, 1, section_type="cover_page"),
        _sec("two", 5, 6, section_type="material_request", document_type="raw_material_request"),
    )
    issues = validate_kv_coverage(seg, [])
    assert issues == []


def test_kv_coverage_skips_unknown_sections() -> None:
    """``unknown`` sections are intentional placeholders from
    ``fill_gaps_with_unknown``; HITL is already aware of them."""

    seg = _seg(_sec("gap", 30, 35, section_type="unknown"))
    issues = validate_kv_coverage(seg, [])
    assert issues == []


def test_kv_coverage_no_warning_when_kv_present() -> None:
    """When any KV pair falls in the section's range, no warning
    — even one is enough to attest the LLM saw real content."""

    seg = _seg(_sec("rm", 50, 55, section_type="material_request",
                    document_type="raw_material_request"))
    kv = [{"page_num": 52, "key": "Material Code", "value": "RM-123"}]
    issues = validate_kv_coverage(seg, kv)
    assert issues == []


def test_kv_coverage_passthrough_when_kv_list_empty() -> None:
    """No KV pairs supplied means we don't know — be silent (don't
    flag every section as missing evidence)."""

    seg = _seg(_sec("long", 1, 20, section_type="manufacturing_operations"))
    issues = validate_kv_coverage(seg, None)
    assert issues == []
    issues_empty = validate_kv_coverage(seg, [])
    assert issues_empty == []


# ── Type consistency ───────────────────────────────────────────


def test_type_mismatch_fires_on_contradictory_pair() -> None:
    """``manufacturing_operations`` is a batch_record sub-section;
    pairing it with ``document_type='ipc_report'`` is a
    contradiction."""

    seg = _seg(_sec(
        "rogue",
        113, 115,
        section_type="manufacturing_operations",
        document_type="ipc_report",
    ))
    issues = validate_type_consistency(seg)
    assert any(i.kind == "type_mismatch" for i in issues)


def test_type_consistency_silent_on_matching_pair() -> None:
    """``manufacturing_operations`` inside a ``batch_record`` is
    canonical — no warning."""

    seg = _seg(_sec(
        "ops",
        1, 20,
        section_type="manufacturing_operations",
        document_type="batch_record",
    ))
    assert validate_type_consistency(seg) == []


def test_type_consistency_silent_on_unknown_section_type() -> None:
    """``unknown`` is a deliberate placeholder; never trigger
    type-mismatch on it."""

    seg = _seg(_sec("gap", 30, 35, section_type="unknown"))
    assert validate_type_consistency(seg) == []


def test_type_consistency_silent_on_whole_doc_as_section() -> None:
    """When section_type matches the doc_type slug (whole-doc-as-
    section pattern preserved by the canonical normaliser), it's
    a valid emission. No warning."""

    seg = _seg(_sec(
        "ipc",
        113, 115,
        section_type="ipc_report",
        document_type="ipc_report",
    ))
    assert validate_type_consistency(seg) == []


def test_type_consistency_silent_on_empty_fields() -> None:
    """Sections with empty document_type get a pass — the
    unknown-doc-type drift validator already covers them."""

    seg = _seg(_sec("incomplete", 1, 5, section_type="cover_page", document_type=""))
    assert validate_type_consistency(seg) == []


# ── Wire-through ───────────────────────────────────────────────


def test_validate_segmentation_threads_kv_pairs() -> None:
    """The public ``validate_segmentation`` accepts
    ``key_value_pairs`` and forwards them to the KV-coverage
    validator."""

    seg = _seg(_sec("rogue", 50, 55, section_type="manufacturing_operations"))
    kv = [{"page_num": 1, "key": "x", "value": "y"}]
    issues = validate_segmentation(seg, total_pages=100, key_value_pairs=kv)
    kinds = {i.kind for i in issues}
    assert "no_kv_evidence" in kinds


def test_validate_segmentation_kv_skipped_when_omitted() -> None:
    """Backward-compat: callers that don't pass ``key_value_pairs``
    don't get spurious ``no_kv_evidence`` warnings."""

    seg = _seg(_sec("rogue", 50, 55, section_type="manufacturing_operations"))
    issues = validate_segmentation(seg, total_pages=100)
    assert all(i.kind != "no_kv_evidence" for i in issues)
