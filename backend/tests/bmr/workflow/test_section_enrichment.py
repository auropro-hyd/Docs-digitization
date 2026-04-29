"""Integration test for the Spec 007 post-extract section enricher.

Drives the full BMR run through :class:`BMRRunService` with a custom
``section_enricher`` wired into the extraction stage. The enricher
hand-builds a :class:`BPCRSectionMap` so we don't need OCR plumbing in
the test — what we're proving is *the integration*: section_id flows
from the enricher onto :class:`ExtractedPage`, then drives the new
v1.1 yield-section rule from the pilot bank, and lands on the
finding's evidence.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.bmr.capabilities.bpcr_section_detect import (
    BPCRSectionMap,
    SectionSpan,
)
from app.bmr.capabilities.bpcr_section_tagger import tag_bpcr_pages
from app.bmr.capabilities.extracted_data import ExtractedPackage
from app.bmr.ingest.models import DocumentPackage
from app.bmr.workflow.service import BMRRunService, StartRunSpec
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
