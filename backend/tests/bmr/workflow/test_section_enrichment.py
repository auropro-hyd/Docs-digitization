"""Integration test for the Spec 007 post-extract section enricher.

Drives the full BMR run through :class:`BMRRunService` with a custom
``section_enricher`` wired into the extraction stage. The enricher
hand-builds a :class:`BPCRSectionMap` so we don't need OCR plumbing in
the test — what we're proving is *the integration*: section_id flows
from the enricher onto :class:`ExtractedPage`, then drives the new
v1.1 yield-section rule from the pilot bank, and lands on the
finding's evidence.

The :func:`test_default_enricher_picks_up_ocr_sidecars_on_disk` test
covers the production wiring path end to end: write
``<package_dir>/ocr/<doc_id>.json`` files exactly the way
:class:`OCRBackedExtractor` does, then construct the service with
:func:`build_default_section_enricher` (the wiring used by
``api/routes/bmr_runs._service``) and verify section_id reaches
findings.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.bmr.capabilities.bpcr_section_detect import (
    BPCRSectionMap,
    SectionSpan,
)
from app.bmr.capabilities.bpcr_section_tagger import tag_bpcr_pages
from app.bmr.capabilities.extracted_data import ExtractedPackage
from app.bmr.ingest.models import DocumentPackage
from app.bmr.workflow.extractor import _ocr_sidecar_path
from app.bmr.workflow.section_enrichment import build_default_section_enricher
from app.bmr.workflow.service import BMRRunService, StartRunSpec
from app.core.ports.ocr import OCRPageResult, OCRResult
from tests.bmr.workflow.conftest import (
    PILOT_RULES_DIR,
    build_classified_package,
    write_extraction_fixture,
)


def _enricher_factory(
    bpcr_doc_id: str, section_id: str = "yield_calculation"
):
    """Return a section_enricher that tags the BPCR's only step page
    (page_index 2) with ``section_id``.
    """

    section_map = BPCRSectionMap(
        doc_id=bpcr_doc_id,
        spec_version="test-1.0",
        method="heuristic",
        outcome="ok",
        spans=[
            SectionSpan(
                section_id=section_id,
                display_name=section_id.replace("_", " ").title(),
                start_page=1,
                end_page=10,
                confidence=1.0,
                detection_method="heuristic_top_of_page",
            )
        ],
    )

    def enricher(
        extracted: ExtractedPackage,
        _package: DocumentPackage,
        _package_dir: Path,
    ) -> ExtractedPackage:
        return tag_bpcr_pages(extracted, section_maps={bpcr_doc_id: section_map})

    return enricher


def test_section_enricher_drives_section_aware_rule(
    package_store, run_store, repo_root, ingest_service
) -> None:
    pkg_id, bpcr_doc_id, rm_doc_id = build_classified_package(ingest_service)
    write_extraction_fixture(
        package_store,
        pkg_id,
        bpcr_doc_id=bpcr_doc_id,
        rm_doc_id=rm_doc_id,
        bpcr_weight_kg=10.0,
        rm_weight_kg=10.0,
        operator_signature="J. Doe",
    )

    service = BMRRunService(
        package_store=package_store,
        run_store=run_store,
        repo_root=repo_root,
        section_enricher=_enricher_factory(bpcr_doc_id),
    )
    report = service.start_run(
        StartRunSpec(package_id=pkg_id, rules_dir=PILOT_RULES_DIR)
    )

    # The yield-section rule from the pilot bank must have evaluated.
    rule_ids = {f.rule_id for f in report.findings}
    assert "alcoa.accurate.bpcr-yield-section-vs-batch-target" in rule_ids


def test_disabling_enrichment_via_env_falls_back_to_unevaluated(
    monkeypatch, package_store, run_store, repo_root, ingest_service
) -> None:
    pkg_id, bpcr_doc_id, rm_doc_id = build_classified_package(ingest_service)
    write_extraction_fixture(
        package_store,
        pkg_id,
        bpcr_doc_id=bpcr_doc_id,
        rm_doc_id=rm_doc_id,
        bpcr_weight_kg=10.0,
        rm_weight_kg=10.0,
        operator_signature="J. Doe",
    )

    monkeypatch.setenv("AT_BMR__BPCR_SECTIONS_ENABLED", "false")

    service = BMRRunService(
        package_store=package_store,
        run_store=run_store,
        repo_root=repo_root,
        section_enricher=_enricher_factory(bpcr_doc_id),  # wired but disabled
    )
    report = service.start_run(
        StartRunSpec(package_id=pkg_id, rules_dir=PILOT_RULES_DIR)
    )

    section_rule_finding = next(
        f for f in report.findings
        if f.rule_id == "alcoa.accurate.bpcr-yield-section-vs-batch-target"
    )
    assert section_rule_finding.status == "unevaluated"


def test_enricher_failure_is_swallowed_and_run_continues(
    package_store, run_store, repo_root, ingest_service
) -> None:
    pkg_id, bpcr_doc_id, rm_doc_id = build_classified_package(ingest_service)
    write_extraction_fixture(
        package_store,
        pkg_id,
        bpcr_doc_id=bpcr_doc_id,
        rm_doc_id=rm_doc_id,
        bpcr_weight_kg=10.0,
        rm_weight_kg=10.0,
        operator_signature="J. Doe",
    )

    def boom(_extracted, _package, _package_dir):
        raise RuntimeError("synthetic failure")

    # Make sure we don't accidentally inherit a disabling env var.
    os.environ.pop("AT_BMR__BPCR_SECTIONS_ENABLED", None)

    service = BMRRunService(
        package_store=package_store,
        run_store=run_store,
        repo_root=repo_root,
        section_enricher=boom,
    )
    report = service.start_run(
        StartRunSpec(package_id=pkg_id, rules_dir=PILOT_RULES_DIR)
    )

    # Run completed; section-aware rule degraded to UNEVALUATED.
    assert report.status == "completed"
    section_rule_finding = next(
        f for f in report.findings
        if f.rule_id == "alcoa.accurate.bpcr-yield-section-vs-batch-target"
    )
    assert section_rule_finding.status == "unevaluated"


def _write_ocr_sidecar(
    package_dir: Path, doc_id: str, ocr: OCRResult
) -> None:
    """Hand-roll the same sidecar shape :class:`OCRBackedExtractor` writes."""

    target = _ocr_sidecar_path(package_dir, doc_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(ocr.model_dump(mode="json"), indent=2), encoding="utf-8"
    )


def test_default_enricher_picks_up_ocr_sidecars_on_disk(
    monkeypatch, package_store, run_store, repo_root, ingest_service
) -> None:
    """Production wiring path: sidecar on disk → factory → section_id on finding.

    Mirrors what ``api/routes/bmr_runs._service`` does in production:
    constructs the enricher via :func:`build_default_section_enricher`
    and lets it discover the OCR sidecar by file convention. The only
    test-specific bit is the synthetic OCR JSON we drop in front of
    it — everything else is the real production code path.
    """

    monkeypatch.delenv("AT_BMR__BPCR_SECTIONS_ENABLED", raising=False)
    monkeypatch.delenv("AT_BMR__BPCR_SECTIONS_SPEC", raising=False)

    pkg_id, bpcr_doc_id, rm_doc_id = build_classified_package(ingest_service)

    # Custom extraction.json — same as the helper, but adds a BMR page
    # with ``batch_target_weight_kg`` so the section-aware rule can
    # actually compute its expected/actual delta. The shared helper
    # doesn't add a BMR page, which is fine for the existing tests
    # (they only need the rule to land in the report) but here we need
    # an end-to-end pass-or-fail signal.
    package_dir = package_store.base_path / pkg_id
    bmr_doc_id = next(
        d.doc_id for d in package_store.load(pkg_id).documents if d.role == "BMR"
    )
    extraction = {
        "package_id": pkg_id,
        "pages": [
            {
                "doc_id": bmr_doc_id,
                "document_role": "BMR",
                "page_index": 1,
                "tags": ["bmr_summary_page"],
                "fields": [
                    {
                        "field": "batch_target_weight_kg",
                        "value": 10.0,
                        "source_doc_id": bmr_doc_id,
                        "source_page_index": 1,
                    }
                ],
            },
            {
                "doc_id": bpcr_doc_id,
                "document_role": "BPCR",
                "page_index": 2,
                "tags": ["bpcr_step_page"],
                "fields": [
                    {
                        "field": "dispensed_weight_kg",
                        "value": 10.0,
                        "entity_name": "Lactose Monohydrate",
                        "source_doc_id": bpcr_doc_id,
                        "source_page_index": 2,
                    },
                    {
                        "field": "operator_signature",
                        "value": "J. Doe",
                        "source_doc_id": bpcr_doc_id,
                        "source_page_index": 2,
                    },
                ],
            },
            {
                "doc_id": rm_doc_id,
                "document_role": "RawMaterialPage",
                "page_index": 1,
                "tags": ["raw_material_page"],
                "fields": [
                    {
                        "field": "weight_kg",
                        "value": 10.0,
                        "entity_name": "Lactose Monohydrate",
                        "source_doc_id": rm_doc_id,
                        "source_page_index": 1,
                    }
                ],
            },
        ],
    }
    extraction_path = package_dir / "extraction.json"
    extraction_path.parent.mkdir(parents=True, exist_ok=True)
    extraction_path.write_text(json.dumps(extraction, indent=2), encoding="utf-8")

    # Drop a markdown-only OCR sidecar where page 2's first line is the
    # plaintext "Yield Calculation" header. The heuristic detector spreads
    # markdown lines uniformly over the page, so the first line lands in
    # the top_of_page band — that's an allowed band for the
    # yield_calculation section in bpcr-section-spec.yaml. Note we keep
    # the lines plain (no `#` heading prefix, no `**bold**` markup):
    # the v0 regexes are anchored at ``^\s*`` and don't strip markdown,
    # so a leading ``#`` or ``**`` would prevent the match.
    ocr = OCRResult(
        pages=[
            OCRPageResult(
                page_num=1,
                markdown="Batch Production and Control Record\n\nCover content.",
            ),
            OCRPageResult(
                page_num=2,
                markdown="Yield Calculation\n\nDispensed weight 10.0 kg",
            ),
        ]
    )
    _write_ocr_sidecar(package_dir, bpcr_doc_id, ocr)

    enricher = build_default_section_enricher()
    assert enricher is not None, "default spec must load with shipped pilot YAML"

    service = BMRRunService(
        package_store=package_store,
        run_store=run_store,
        repo_root=repo_root,
        section_enricher=enricher,
    )
    report = service.start_run(
        StartRunSpec(package_id=pkg_id, rules_dir=PILOT_RULES_DIR)
    )

    yield_finding = next(
        f for f in report.findings
        if f.rule_id == "alcoa.accurate.bpcr-yield-section-vs-batch-target"
    )

    assert yield_finding.status != "unevaluated", (
        "section detection should have tagged page 2 as yield_calculation, "
        "letting the rule aggregate dispensed_weight_kg from a non-empty page set"
    )
    aggregated_evidence = [
        ev for ev in yield_finding.evidence
        if ev.note == "source_aggregated"
    ]
    assert aggregated_evidence, (
        "expected at least one source_aggregated evidence entry from the "
        "yield_calculation section page"
    )
    assert all(ev.section_id == "yield_calculation" for ev in aggregated_evidence)


def test_default_enricher_logs_warning_when_sidecar_missing(
    monkeypatch, package_store, run_store, repo_root, ingest_service, caplog
) -> None:
    """No OCR sidecar on disk → enricher logs once per BPCR doc and continues.

    Common pilot scenario before the upstream OCR pipeline starts
    dropping sidecars. We must not crash the run, and the section-aware
    rule must degrade per its existing fallback policy.
    """

    monkeypatch.delenv("AT_BMR__BPCR_SECTIONS_ENABLED", raising=False)

    pkg_id, bpcr_doc_id, rm_doc_id = build_classified_package(ingest_service)
    write_extraction_fixture(
        package_store,
        pkg_id,
        bpcr_doc_id=bpcr_doc_id,
        rm_doc_id=rm_doc_id,
        bpcr_weight_kg=10.0,
        rm_weight_kg=10.0,
        operator_signature="J. Doe",
    )

    enricher = build_default_section_enricher()
    assert enricher is not None

    service = BMRRunService(
        package_store=package_store,
        run_store=run_store,
        repo_root=repo_root,
        section_enricher=enricher,
    )
    with caplog.at_level("WARNING", logger="app.bmr.workflow.section_enrichment"):
        report = service.start_run(
            StartRunSpec(package_id=pkg_id, rules_dir=PILOT_RULES_DIR)
        )

    assert any(
        "no_ocr_sidecar" in rec.message and bpcr_doc_id in rec.message
        for rec in caplog.records
    ), "enricher must log a structured warning naming the missing doc"

    yield_finding = next(
        f for f in report.findings
        if f.rule_id == "alcoa.accurate.bpcr-yield-section-vs-batch-target"
    )
    assert yield_finding.status == "unevaluated"
