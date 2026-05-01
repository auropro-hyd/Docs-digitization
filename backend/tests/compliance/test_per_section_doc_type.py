"""Tests for per-section document type classification.

Design decision: normalization of document_type happens in build_page_to_section()
(in segmentation.py), NOT as a Pydantic field validator on DocumentSection.
"""
from __future__ import annotations

import pytest
from app.compliance.models import DocumentSection, DocumentSegmentation
from app.compliance.segmentation import build_page_to_section
from app.compliance.rules.profiles import normalize_document_type


class TestDocumentSectionDocumentType:
    """Model behaviour — no field validator on DocumentSection."""

    def test_empty_string_stays_empty(self):
        sec = DocumentSection(section_type="manufacturing_operations", document_type="")
        assert sec.document_type == ""

    def test_omitted_defaults_to_empty(self):
        sec = DocumentSection(section_type="manufacturing_operations")
        assert sec.document_type == ""

    def test_canonical_key_passes_through(self):
        sec = DocumentSection(section_type="manufacturing_operations", document_type="batch_record")
        assert sec.document_type == "batch_record"


class TestBuildPageToSection:
    """Normalization happens inside build_page_to_section."""

    def _make_seg(self, document_type: str, start_page: int = 1, end_page: int = 3) -> DocumentSegmentation:
        return DocumentSegmentation(
            sections=[
                DocumentSection(
                    section_id="sec1",
                    name="Test Section",
                    section_type="manufacturing_operations",
                    document_type=document_type,
                    start_page=start_page,
                    end_page=end_page,
                )
            ],
            document_type="batch_record",
            confidence=0.9,
        )

    def test_build_page_to_section_includes_document_type(self):
        """Canonical document_type is preserved in the page map."""
        seg = self._make_seg("batch_record")
        page_map = build_page_to_section(seg)
        assert page_map[1]["document_type"] == "batch_record"

    def test_build_page_to_section_resolves_alias(self):
        """'bmr' alias is resolved to canonical 'batch_record'."""
        seg = self._make_seg("bmr")
        page_map = build_page_to_section(seg)
        assert page_map[1]["document_type"] == "batch_record"

    def test_build_page_to_section_unrecognized_preserved(self):
        """Unrecognized document_type is preserved as-is (not collapsed to empty)."""
        seg = self._make_seg("logbook")
        page_map = build_page_to_section(seg)
        # normalize_document_type returns the value unchanged when not found
        assert page_map[1]["document_type"] == "logbook"

    def test_build_page_to_section_empty_preserved(self):
        """Empty document_type stays empty — normalize is skipped."""
        seg = self._make_seg("")
        page_map = build_page_to_section(seg)
        assert page_map[1]["document_type"] == ""

    def test_old_segmentation_json_no_document_type_field(self):
        """T008: DocumentSegmentation loaded from JSON without document_type key on
        sections (legacy format) — all sections default document_type to ''."""
        data = {
            "sections": [
                {
                    "section_id": "s1",
                    "name": "Cover Page",
                    "section_type": "cover_page",
                    "start_page": 1,
                    "end_page": 2,
                    # no document_type key — simulates old segmentation.json
                }
            ],
            "document_type": "batch_record",
            "confidence": 0.8,
        }
        seg = DocumentSegmentation.model_validate(data)
        assert seg.sections[0].document_type == ""

    def test_effective_doc_type_fallback(self):
        """Inline logic test: effective doc type falls back to 'batch_record' when
        section document_type is empty — no evaluator import required."""
        sec_info = {"document_type": ""}
        effective = (sec_info or {}).get("document_type") or "batch_record"
        assert effective == "batch_record"

        sec_info_none = None
        effective_none = (sec_info_none or {}).get("document_type") or "batch_record"
        assert effective_none == "batch_record"

        sec_info_set = {"document_type": "scada_report"}
        effective_set = (sec_info_set or {}).get("document_type") or "batch_record"
        assert effective_set == "scada_report"


class TestNormalizeDocumentType:
    """T007 coverage: normalize_document_type resolves aliases from document_profiles.yaml."""

    def test_bmr_resolves_to_batch_record(self):
        """'bmr' is listed as an alias for batch_record in document_profiles.yaml."""
        assert normalize_document_type("bmr") == "batch_record"

    def test_canonical_passes_through(self):
        assert normalize_document_type("batch_record") == "batch_record"

    def test_unknown_value_returned_as_is(self):
        """Values not in any profile are returned verbatim (slugified), not collapsed."""
        assert normalize_document_type("logbook") == "logbook"
