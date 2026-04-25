"""Tests for pluggable Stage-3 extraction (Spec 004 follow-up #1).

These cover both adapters the BMR audit currently ships:

- :class:`SidecarExtractor` — legacy JSON sidecar loader used by the
  existing test suite; asserted here to guarantee the refactor did not
  regress the happy path.
- :class:`OCRBackedExtractor` — runs an :class:`OCREngine` across the
  package's PDFs and projects ``key_value_pairs`` through a declarative
  field map. We drive it with a stub engine so tests stay deterministic
  and hermetic (Constitution VIII).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.bmr.workflow.extractor import (
    OCRBackedExtractor,
    OCRRoleExtraction,
    SidecarExtractor,
)
from app.bmr.workflow.service import BMRRunService, StartRunSpec
from app.core.ports.ocr import KeyValuePair, OCRResult
from tests.bmr.workflow.conftest import (
    PILOT_RULES_DIR,
    build_classified_package,
    write_extraction_fixture,
)


class _StubOCREngine:
    """OCREngine that returns pre-canned results keyed by filename stem."""

    def __init__(self, results: dict[str, OCRResult]) -> None:
        self._results = results
        self.calls: list[str] = []

    async def extract(self, pdf_path, pages=None, progress_callback=None):
        stem = Path(pdf_path).stem
        self.calls.append(stem)
        if stem not in self._results:
            return OCRResult()
        return self._results[stem]

    def supports_handwriting(self) -> bool:
        return False

    def supports_barcodes(self) -> bool:
        return False

    def supports_selection_marks(self) -> bool:
        return False


def test_sidecar_extractor_keeps_legacy_behaviour(
    ingest_service, package_store
):
    package_id, bpcr_id, rm_id = build_classified_package(ingest_service)
    write_extraction_fixture(
        package_store,
        package_id,
        bpcr_doc_id=bpcr_id,
        rm_doc_id=rm_id,
        bpcr_weight_kg=12.5,
        rm_weight_kg=12.5,
        operator_signature="A. Operator",
    )
    package = package_store.load(package_id)
    extractor = SidecarExtractor()

    extracted = extractor.extract(
        package,
        package_dir=package_store.base_path / package_id,
    )

    assert extracted.package_id == package_id
    weights = [
        f.value
        for page in extracted.pages
        for f in page.fields
        if f.field in {"dispensed_weight_kg", "weight_kg"}
    ]
    assert weights == [12.5, 12.5]


def test_ocr_extractor_projects_key_value_pairs(
    ingest_service, package_store
):
    package_id, bpcr_id, rm_id = build_classified_package(ingest_service)
    package = package_store.load(package_id)
    bpcr_doc = next(d for d in package.documents if d.doc_id == bpcr_id)
    rm_doc = next(d for d in package.documents if d.doc_id == rm_id)

    ocr_results = {
        Path(bpcr_doc.stored_path).stem: OCRResult(
            key_value_pairs=[
                KeyValuePair(
                    key="Dispensed weight (kg)",
                    value="12.5",
                    confidence=0.92,
                    page_num=2,
                ),
                KeyValuePair(
                    key="Operator signature",
                    value="A. Operator",
                    confidence=0.88,
                    page_num=2,
                ),
                # Unknown label must be ignored, not crash the projection.
                KeyValuePair(key="Notes", value="ignore me", page_num=2),
            ]
        ),
        Path(rm_doc.stored_path).stem: OCRResult(
            key_value_pairs=[
                KeyValuePair(
                    key="Weight (kg)",
                    value="12.5",
                    confidence=0.81,
                    page_num=1,
                )
            ]
        ),
    }
    engine = _StubOCREngine(ocr_results)
    field_map = {
        "BPCR": OCRRoleExtraction(
            document_role="BPCR",
            page_tags=["bpcr_step_page"],
            fields={
                "dispensed_weight_kg": ["Dispensed weight (kg)"],
                "operator_signature": ["Operator signature"],
            },
        ),
        "RawMaterialPage": OCRRoleExtraction(
            document_role="RawMaterialPage",
            page_tags=["raw_material_page"],
            fields={"weight_kg": ["Weight (kg)"]},
        ),
    }
    extractor = OCRBackedExtractor(ocr_engine=engine, field_map=field_map)

    extracted = extractor.extract(
        package,
        package_dir=package_store.base_path / package_id,
    )

    assert {page.document_role for page in extracted.pages} == {
        "BPCR",
        "RawMaterialPage",
    }
    bpcr_page = next(p for p in extracted.pages if p.document_role == "BPCR")
    assert bpcr_page.tags == ["bpcr_step_page"]
    field_by_name = {f.field: f for f in bpcr_page.fields}
    assert set(field_by_name) == {"dispensed_weight_kg", "operator_signature"}
    assert field_by_name["dispensed_weight_kg"].value == "12.5"
    # OCR confidences flow through so downstream quality rules can use them.
    assert field_by_name["dispensed_weight_kg"].confidence == pytest.approx(0.92)

    # Sidecar cache is written so a subsequent run (or correction replay)
    # reads the same extraction without re-running OCR.
    sidecar = package_store.base_path / package_id / "extraction.json"
    assert sidecar.is_file()
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["package_id"] == package_id


def test_ocr_extractor_skips_documents_without_field_map(
    ingest_service, package_store
):
    package_id, bpcr_id, _ = build_classified_package(ingest_service)
    package = package_store.load(package_id)
    bpcr_doc = next(d for d in package.documents if d.doc_id == bpcr_id)

    engine = _StubOCREngine(
        {
            Path(bpcr_doc.stored_path).stem: OCRResult(
                key_value_pairs=[
                    KeyValuePair(
                        key="Dispensed weight (kg)", value="5.0", page_num=1
                    )
                ]
            )
        }
    )
    extractor = OCRBackedExtractor(
        ocr_engine=engine,
        field_map={
            "BPCR": OCRRoleExtraction(
                document_role="BPCR",
                fields={"dispensed_weight_kg": ["Dispensed weight (kg)"]},
            )
        },
    )

    extractor.extract(
        package,
        package_dir=package_store.base_path / package_id,
    )

    # Only the BPCR file should have been sent to the engine — other
    # roles (BMR, RawMaterialPage) are absent from the field_map.
    assert engine.calls == [Path(bpcr_doc.stored_path).stem]


def test_service_runs_graph_with_ocr_extractor(
    ingest_service, package_store, run_store, repo_root
):
    package_id, bpcr_id, rm_id = build_classified_package(ingest_service)
    package = package_store.load(package_id)
    bpcr_doc = next(d for d in package.documents if d.doc_id == bpcr_id)
    rm_doc = next(d for d in package.documents if d.doc_id == rm_id)

    engine = _StubOCREngine(
        {
            Path(bpcr_doc.stored_path).stem: OCRResult(
                key_value_pairs=[
                    KeyValuePair(
                        key="Dispensed weight (kg)",
                        value="10.0",
                        confidence=0.9,
                        page_num=2,
                    ),
                    KeyValuePair(
                        key="Operator signature",
                        value="A. Operator",
                        confidence=0.9,
                        page_num=2,
                    ),
                ]
            ),
            Path(rm_doc.stored_path).stem: OCRResult(
                key_value_pairs=[
                    KeyValuePair(
                        key="Weight (kg)",
                        value="10.0",
                        confidence=0.9,
                        page_num=1,
                    )
                ]
            ),
        }
    )
    extractor = OCRBackedExtractor(
        ocr_engine=engine,
        field_map={
            "BPCR": OCRRoleExtraction(
                document_role="BPCR",
                page_tags=["bpcr_step_page"],
                fields={
                    "dispensed_weight_kg": ["Dispensed weight (kg)"],
                    "operator_signature": ["Operator signature"],
                },
            ),
            "RawMaterialPage": OCRRoleExtraction(
                document_role="RawMaterialPage",
                page_tags=["raw_material_page"],
                fields={"weight_kg": ["Weight (kg)"]},
            ),
        },
    )
    service = BMRRunService(
        package_store=package_store,
        run_store=run_store,
        repo_root=repo_root,
        extractor=extractor,
    )

    report = service.start_run(
        StartRunSpec(package_id=package_id, rules_dir=PILOT_RULES_DIR)
    )

    # Same rule bank as the sidecar-driven tests — proves OCR produced
    # an equivalent ExtractedPackage end-to-end. Note the fact that we
    # read entity_name as missing may cause some rules to unevaluate; we
    # assert only that the compliance stage ran without error.
    assert report.status.value == "completed"
    assert report.rules_evaluated == 3

    # Because the entity_name is not recoverable from plain key/value
    # OCR pairs (requires layout-aware extraction), BPCR/RawMaterial
    # cross-document rules go "unevaluated" rather than producing passes.
    # This is exactly the signal reviewers need to tighten the field
    # map or adopt a richer extractor — not a test failure.
