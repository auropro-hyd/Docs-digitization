"""Pin the deterministic document_type inference + stamping contract.

``infer_document_type_for_section_type`` is the single source of
truth for "which document profile owns this section_type". The
segmentation post-processor ``stamp_document_types`` uses it to
fill empty ``DocumentSection.document_type`` fields without an
LLM call. Two invariants matter most:

* **Single-owner sections resolve unambiguously.** A section_type
  listed in exactly one profile's ``expected_sections`` returns
  that profile.
* **Ambiguous or unknown section_types return None**, so the
  caller leaves the field empty and the cross-document filter
  degrades to section-type-only matching rather than guessing
  the wrong owner.
"""

from __future__ import annotations

import pytest

from app.compliance.models import DocumentSection, DocumentSegmentation
from app.compliance.rules.profiles import (
    infer_document_type_for_section_type,
    load_profiles,
)
from app.compliance.segmentation import stamp_document_types


@pytest.fixture(autouse=True)
def _reset_profiles_cache():
    load_profiles.cache_clear()
    yield
    load_profiles.cache_clear()


# ── Inference ─────────────────────────────────────────────────


@pytest.mark.parametrize("section_type,expected_doc_type", [
    ("manufacturing_operations", "batch_record"),
    ("material_dispensing", "batch_record"),
    ("yield_calculation", "batch_record"),
    ("cleaning_log", "batch_record"),
    ("material_request", "raw_material_request"),
    ("material_issue", "raw_material_request"),
])
def test_infer_resolves_sub_section_to_owning_profile(
    section_type: str, expected_doc_type: str,
) -> None:
    assert infer_document_type_for_section_type(section_type) == expected_doc_type


@pytest.mark.parametrize("section_type,expected_doc_type", [
    # Whole-document-as-section: the section_type IS a document_type slug.
    ("batch_record", "batch_record"),
    ("scada_report", "scada_report"),
    # Aliases of document_types resolve too — Akhilesh's BPCR title
    # alias and the in-process report alias.
    ("batch_production_and_control_record", "batch_record"),
    ("ipc_report", "ipc_report"),
    ("in_process_report", "ipc_report"),
])
def test_infer_resolves_whole_document_section(
    section_type: str, expected_doc_type: str,
) -> None:
    assert infer_document_type_for_section_type(section_type) == expected_doc_type


@pytest.mark.parametrize("section_type", [
    "totally_unknown_section",
    "",
    "some_random_string",
])
def test_infer_returns_none_for_unknown(section_type: str) -> None:
    assert infer_document_type_for_section_type(section_type) is None


def test_infer_returns_none_for_ambiguous_section_type() -> None:
    """``certificate_of_analysis`` is listed under both ``certificate``
    and ``qc_analytical_package`` profiles. The inference must
    refuse to guess — returning None lets the segmentation LLM
    (which has more context) own the disambiguation.
    """
    assert infer_document_type_for_section_type("certificate_of_analysis") is None


# ── stamping post-processor ───────────────────────────────────


def test_stamp_fills_empty_document_type_via_inference() -> None:
    seg = DocumentSegmentation(
        sections=[
            DocumentSection(section_id="s1", section_type="material_request",
                            start_page=1, end_page=2),
            DocumentSection(section_id="s2", section_type="manufacturing_operations",
                            start_page=3, end_page=10),
        ]
    )
    stamped = stamp_document_types(seg)
    by_id = {s.section_id: s for s in stamped.sections}
    assert by_id["s1"].document_type == "raw_material_request"
    assert by_id["s2"].document_type == "batch_record"


def test_stamp_preserves_explicit_document_type() -> None:
    """If the LLM (or a previous pass) already populated document_type,
    the stamp must not overwrite it — even when the inference
    would disagree."""
    seg = DocumentSegmentation(
        sections=[
            DocumentSection(
                section_id="s1",
                section_type="manufacturing_operations",
                document_type="custom_pipeline_v2",
                start_page=1, end_page=2,
            ),
        ]
    )
    stamped = stamp_document_types(seg)
    assert stamped.sections[0].document_type == "custom_pipeline_v2"


def test_stamp_uses_bpcr_hint_before_inference() -> None:
    """A section_type substring that triggers the BPCR detector
    (``bpcr``, ``batch_record``, etc.) must stamp ``batch_record``
    even when the section_type itself isn't in any profile's
    expected_sections — otherwise BPCR-classified opaque sections
    would lose their document_type stamp."""
    seg = DocumentSegmentation(
        sections=[
            # Hypothetical free-form classification from the segmentation LLM.
            DocumentSection(section_id="s1", section_type="bpcr_main_body",
                            start_page=1, end_page=20),
        ]
    )
    stamped = stamp_document_types(seg)
    assert stamped.sections[0].document_type == "batch_record"


def test_stamp_leaves_ambiguous_section_type_empty() -> None:
    """Ambiguous section_types must remain empty — the cross-document
    filter then degrades to section-type-only matching, which is
    the safe default. The alternative (guessing) silently mislabels
    sections and breaks every cross-document rule that filters by
    document_type."""
    seg = DocumentSegmentation(
        sections=[
            DocumentSection(section_id="s1", section_type="certificate_of_analysis",
                            start_page=1, end_page=2),
        ]
    )
    stamped = stamp_document_types(seg)
    assert stamped.sections[0].document_type == ""


def test_stamp_is_idempotent() -> None:
    seg = DocumentSegmentation(
        sections=[
            DocumentSection(section_id="s1", section_type="material_request",
                            start_page=1, end_page=2),
        ]
    )
    once = stamp_document_types(seg)
    twice = stamp_document_types(once)
    assert once.model_dump() == twice.model_dump()


def test_stamp_returns_new_instance_does_not_mutate() -> None:
    seg = DocumentSegmentation(
        sections=[
            DocumentSection(section_id="s1", section_type="material_request",
                            start_page=1, end_page=2),
        ]
    )
    original_doc_type = seg.sections[0].document_type
    _ = stamp_document_types(seg)
    assert seg.sections[0].document_type == original_doc_type
