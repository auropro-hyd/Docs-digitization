"""Spec 007 follow-up — markdown-fallback + RunReport surfacing.

These cover the scenario Akhilesh hit on a real BPCR run: the package
flowed through ``SidecarExtractor`` (so no OCR sidecar was ever
written), and section detection silently no-op'd because the
production enricher's only input source was the missing sidecar.

The fix has three moving parts that this file exercises end-to-end:

1. :class:`~app.bmr.capabilities.extracted_data.ExtractedPage` carries
   a ``text`` field that ``extraction.json`` can populate per page.
2. :class:`_ProductionSectionEnricher` falls back to synthesising an
   :class:`~app.core.ports.ocr.OCRResult` from the per-page text when
   no sidecar is present, so detection still runs (FR-016).
3. :func:`report_stage` projects the resulting ``section_id`` onto
   :class:`~app.bmr.workflow.models.RunReport.bpcr_sections` so
   reviewers can see the assignment without re-running a CLI.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from app.bmr.workflow.section_enrichment import build_default_section_enricher
from app.bmr.workflow.service import BMRRunService, StartRunSpec
from tests.bmr.workflow.conftest import (
    BACKEND_ROOT,
    PILOT_RULES_DIR,
    build_classified_package,
)


def _write_extraction_with_text(
    package_dir: Path,
    *,
    package_id: str,
    bmr_doc_id: str,
    bpcr_doc_id: str,
    rm_doc_id: str,
    bpcr_page_text: dict[int, str],
) -> None:
    """Hand-roll an extraction.json that carries per-page markdown.

    Mirrors the shape ``write_extraction_fixture`` produces but adds a
    ``text`` entry per BPCR page so the enricher can synthesise OCR
    when no sidecar is present.
    """

    bpcr_pages = []
    for page_index, text in sorted(bpcr_page_text.items()):
        bpcr_pages.append(
            {
                "doc_id": bpcr_doc_id,
                "document_role": "BPCR",
                "page_index": page_index,
                "tags": ["bpcr_step_page"] if page_index == 2 else [],
                "fields": (
                    [
                        {
                            "field": "dispensed_weight_kg",
                            "value": 10.0,
                            "entity_name": "Lactose Monohydrate",
                            "source_doc_id": bpcr_doc_id,
                            "source_page_index": page_index,
                        },
                        {
                            "field": "operator_signature",
                            "value": "J. Doe",
                            "source_doc_id": bpcr_doc_id,
                            "source_page_index": page_index,
                        },
                    ]
                    if page_index == 2
                    else []
                ),
                "text": text,
            }
        )
    extraction = {
        "package_id": package_id,
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
            *bpcr_pages,
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
    target = package_dir / "extraction.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(extraction, indent=2), encoding="utf-8")


def test_markdown_fallback_runs_detector_without_ocr_sidecar(
    monkeypatch, package_store, run_store, repo_root, ingest_service
) -> None:
    """No sidecar + per-page text in extraction.json → detector still runs.

    This is the path Akhilesh's pilot package took. Before the fix,
    the enricher logged ``no_ocr_sidecar`` and skipped the doc; the
    section-aware rule degraded to ``unevaluated`` with no surfaced
    section assignment. After the fix, the synthesised OCR drives the
    detector and ``section_id`` lands on the BPCR pages and the
    finding's evidence.
    """

    monkeypatch.delenv("AT_BMR__BPCR_SECTIONS_ENABLED", raising=False)
    monkeypatch.delenv("AT_BMR__BPCR_SECTIONS_SPEC", raising=False)

    pkg_id, bpcr_doc_id, rm_doc_id = build_classified_package(ingest_service)
    package_dir = package_store.base_path / pkg_id
    bmr_doc_id = next(
        d.doc_id for d in package_store.load(pkg_id).documents if d.role == "BMR"
    )

    _write_extraction_with_text(
        package_dir,
        package_id=pkg_id,
        bmr_doc_id=bmr_doc_id,
        bpcr_doc_id=bpcr_doc_id,
        rm_doc_id=rm_doc_id,
        bpcr_page_text={
            1: "Batch Production and Control Record\n\nCover content.",
            2: "Yield Calculation\n\nDispensed weight 10.0 kg",
        },
    )

    # Confirm we're really exercising the fallback — no OCR sidecar
    # has been written. If this assertion ever fires it means the
    # test environment is leaking state from another fixture.
    assert not (package_dir / "ocr").exists(), (
        "fallback test must run with no OCR sidecar present"
    )

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

    # 1. Per-page summary on the report shows the synthesised assignment.
    assert report.bpcr_sections, (
        "RunReport.bpcr_sections must be populated when detection runs"
    )
    by_page = {row["page_index"]: row for row in report.bpcr_sections}
    assert by_page[2]["section_id"] == "yield_calculation"
    assert by_page[2]["doc_id"] == bpcr_doc_id

    # 2. The section-aware rule must have something to evaluate now —
    # the markdown-fallback path is the *only* thing standing between
    # "detection didn't run" and "rule sees a section_id". This is the
    # signal Akhilesh was missing in production.
    yield_finding = next(
        f for f in report.findings
        if f.rule_id == "alcoa.accurate.bpcr-yield-section-vs-batch-target"
    )
    assert yield_finding.status != "unevaluated", (
        "fallback path failed: rule did not see the synthesised section_id"
    )


def test_pilot_section_spec_matches_authoritative_profile_vocabulary() -> None:
    """The 13 sections in the pilot YAML must match the authoritative profile.

    ``document_profiles.yaml::batch_record.expected_sections`` is the
    contract reviewers and rule authors use. If the detector spec
    drifts from it (as it did before this fix — detector had 10
    fictional sections, profile had 13 real ones), section-aware
    rules silently never fire because no rule's ``section_type``
    can match a detector ``section_id``.
    """

    spec_yaml = (
        BACKEND_ROOT / "config" / "bmr" / "pilot" / "bpcr-section-spec.yaml"
    )
    profiles_yaml = (
        BACKEND_ROOT / "app" / "compliance" / "rules" / "document_profiles.yaml"
    )

    spec = yaml.safe_load(spec_yaml.read_text(encoding="utf-8"))
    profiles = yaml.safe_load(profiles_yaml.read_text(encoding="utf-8"))

    spec_ids = {section["section_id"] for section in spec["sections"]}
    profile_ids = {
        section["section_type"]
        for section in profiles["document_profiles"]["batch_record"][
            "expected_sections"
        ]
    }

    missing_in_spec = profile_ids - spec_ids
    extra_in_spec = spec_ids - profile_ids
    assert not missing_in_spec, (
        f"detector spec is missing authoritative sections: "
        f"{sorted(missing_in_spec)}"
    )
    assert not extra_in_spec, (
        f"detector spec carries fictional sections not in document_profiles: "
        f"{sorted(extra_in_spec)}"
    )


def test_report_bpcr_sections_empty_when_detection_disabled(
    monkeypatch, package_store, run_store, repo_root, ingest_service
) -> None:
    """Flag-off → no enrichment → empty bpcr_sections summary on report.

    Belt-and-braces: the report stage must not invent section
    assignments when the enricher never ran. An empty list is the
    operator's signal that detection didn't fire (vs. a populated
    list where every page is ``unsectioned``, which means it ran
    but found nothing).
    """

    monkeypatch.setenv("AT_BMR__BPCR_SECTIONS_ENABLED", "false")

    pkg_id, bpcr_doc_id, rm_doc_id = build_classified_package(ingest_service)
    package_dir = package_store.base_path / pkg_id
    bmr_doc_id = next(
        d.doc_id for d in package_store.load(pkg_id).documents if d.role == "BMR"
    )
    _write_extraction_with_text(
        package_dir,
        package_id=pkg_id,
        bmr_doc_id=bmr_doc_id,
        bpcr_doc_id=bpcr_doc_id,
        rm_doc_id=rm_doc_id,
        bpcr_page_text={2: "Yield Calculation\n\nDispensed weight 10.0 kg"},
    )

    enricher = build_default_section_enricher()
    service = BMRRunService(
        package_store=package_store,
        run_store=run_store,
        repo_root=repo_root,
        section_enricher=enricher,
    )
    report = service.start_run(
        StartRunSpec(package_id=pkg_id, rules_dir=PILOT_RULES_DIR)
    )

    assert report.bpcr_sections == []
