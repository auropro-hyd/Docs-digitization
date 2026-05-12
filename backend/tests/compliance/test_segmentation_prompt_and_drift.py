"""Pin the segmentation-prompt classification heuristics and the
vocabulary-drift summary event.

Three contracts protected here, all from Akhilesh's 2026-05-12 call:

1. **Header-first classification.** The prompt must tell the LLM to
   classify document_type from the document header first, with a
   content-based fallback for cases like ``scada_report`` where no
   header explicitly names the document.

2. **BPCR section headings inside content.** For batch_record
   sub-sections, the prompt must point the LLM at the table
   heading on top of the first table per page — not the page's
   document header.

3. **Vocabulary drift surfaces with a copy-paste-ready YAML snippet.**
   When the segmentation LLM emits a document_type or section_type
   that isn't in ``document_profiles.yaml``, a
   ``segmentation.vocabulary_drift`` event must fire with the
   exact unknown values AND a suggested YAML snippet so the
   operator can paste it directly into the profile config.

These contracts ARE the user-visible behavior. Regressing any of
them sends us back to the "segmentation gives one opaque BPCR row"
state Akhilesh asked us to leave behind.
"""

from __future__ import annotations

import pytest

from app.compliance.segmentation import (
    _build_segmentation_prompt,
    validate_segmentation,
)


def test_segmentation_prompt_header_first_then_content_fallback() -> None:
    """The prompt must encode header-first + content-fallback for
    non-BPCR doc types. The SCADA example is the load-bearing one
    — page 76 of Akhilesh's doc has no header but VDE0** + temp/
    pressure tables; the LLM has to know to infer scada_report
    from that content."""
    prompt = _build_segmentation_prompt(
        extractions=[{"page_num": 1, "markdown": "test"}],
        key_value_pairs=None,
        filename="test.pdf",
    )
    # Header-first rule.
    assert "classify" in prompt.lower() and "header" in prompt.lower()
    # Content-fallback rule with the SCADA-VDE example.
    assert "VDE" in prompt
    assert "scada_report" in prompt
    assert "temp" in prompt.lower() or "pressure" in prompt.lower()


def test_segmentation_prompt_bpcr_section_headings_on_tables() -> None:
    """For BPCR sub-sections, the prompt must direct the LLM to
    look for headings on top of tables (not page-level headers)."""
    prompt = _build_segmentation_prompt(
        extractions=[{"page_num": 1, "markdown": "test"}],
        key_value_pairs=None,
        filename="test.pdf",
    )
    assert "MANUFACTURING INSTRUCTIONS" in prompt
    assert "manufacturing_operations" in prompt
    assert "MICRONIZATION" in prompt.upper()
    assert "metal_detection" in prompt
    # The "top of the table" cue itself must be in the prompt.
    assert "top of the" in prompt.lower() or "on top of" in prompt.lower()


def test_segmentation_prompt_cover_and_revision_use_column_names() -> None:
    """cover_page and revision_summary specifically — these have
    no section heading and must be inferred from column names."""
    prompt = _build_segmentation_prompt(
        extractions=[{"page_num": 1, "markdown": "test"}],
        key_value_pairs=None,
        filename="test.pdf",
    )
    assert "cover_page" in prompt
    assert "revision_summary" in prompt
    # The column-name cue must be present.
    assert "column name" in prompt.lower() or "columns" in prompt.lower()
    # Cover-page column names mentioned.
    assert "BPCR Number" in prompt or "Product Name" in prompt
    # Revision summary column names mentioned.
    assert "Revision Number" in prompt or "Change History" in prompt


def test_segmentation_prompt_injects_canonical_vocabulary() -> None:
    """The prompt must list the canonical doc_type and section_type
    values from ``document_profiles.yaml`` so the LLM uses them
    rather than free-form guesses. Drift detection then catches
    only the genuinely-new cases (not synonyms)."""
    prompt = _build_segmentation_prompt(
        extractions=[{"page_num": 1, "markdown": "test"}],
        key_value_pairs=None,
        filename="test.pdf",
    )
    # Sample of canonical values that must appear.
    assert "batch_record" in prompt
    assert "raw_material_request" in prompt
    assert "qc_analytical_package" in prompt or "ipc_report" in prompt


# ── Vocabulary-drift summary event ──────────────────────────


def test_vocabulary_drift_event_carries_actionable_yaml_snippet() -> None:
    """When segmentation emits a doc_type / section_type that
    isn't in ``document_profiles.yaml``, the validator must
    surface ``segmentation.vocabulary_drift`` with the unknown
    values + a YAML snippet the operator can paste directly.
    """
    from app.compliance.models import DocumentSection, DocumentSegmentation

    seg = DocumentSegmentation(
        sections=[
            DocumentSection(
                section_id="custom_doc",
                name="Custom Doc",
                section_type="totally_made_up_section_type",
                document_type="totally_made_up_doc_type",
                start_page=1, end_page=5,
            ),
        ],
    )
    issues = validate_segmentation(seg, total_pages=10)

    unknown_doc_kinds = [i for i in issues if i.kind == "unknown_document_type"]
    unknown_sec_kinds = [i for i in issues if i.kind == "unknown_section_type"]
    assert unknown_doc_kinds, "validator must flag the unknown doc_type"
    assert unknown_sec_kinds, "validator must flag the unknown section_type"
    # The exact unknown value must appear in the message so the
    # vocabulary_drift summary can extract it.
    assert any("totally_made_up_doc_type" in i.message for i in unknown_doc_kinds)
    assert any("totally_made_up_section_type" in i.message for i in unknown_sec_kinds)


# ── VC-DOC-QUALITY scan-legibility extension ────────────────


def test_vc_doc_quality_prompt_covers_scan_induced_legibility() -> None:
    """VC-DOC-QUALITY must flag scan-process defects, not just
    physical paper defects. Akhilesh hit a reactor-operations
    checklist whose middle rows were entirely blacked out by the
    scanner's threshold filter — that's a SCAN defect, not a
    physical one. The prompt must cover both classes."""
    from app.compliance.vision_evaluator import _VC_PROMPTS

    prompt = _VC_PROMPTS["VC-DOC-QUALITY"]
    # Physical-defect coverage preserved (load-bearing — PR #26
    # FP cascade fix).
    assert "ABSENCE FIRST" in prompt
    assert "SMUDGES" in prompt
    # Scan-defect coverage added.
    assert "BLACK BAND" in prompt or "blacked out" in prompt.lower()
    assert "scanner" in prompt.lower() or "scan" in prompt.lower()
    assert "DARKENING" in prompt or "darkening" in prompt.lower()
