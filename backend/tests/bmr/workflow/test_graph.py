"""End-to-end tests for the 5-stage BMR audit graph.

These tests exercise the compiled LangGraph against the real pilot rule
bank, the real pilot aliases file, and a real package laid out on disk
via :class:`PackageIngestService`. Only the extraction step uses a
hand-rolled ``extraction.json`` fixture.
"""

from __future__ import annotations

from pathlib import Path

from app.bmr.capabilities.evidence import FindingStatus
from app.bmr.ingest.package_store import PackageStore
from app.bmr.ingest.service import PackageIngestService
from app.bmr.workflow.models import RunStage, RunStatus
from app.bmr.workflow.service import BMRRunService, StartRunSpec
from tests.bmr.workflow.conftest import (
    build_classified_package,
    write_extraction_fixture,
)


def _finding_for_rule(report, rule_id: str):
    return [f for f in report.findings if f.rule_id == rule_id]


def test_graph_pass_and_fail_findings(
    run_service: BMRRunService,
    ingest_service: PackageIngestService,
    package_store: PackageStore,
    pilot_rules_dir: Path,
):
    package_id, bpcr_id, rm_id = build_classified_package(ingest_service)
    write_extraction_fixture(
        package_store,
        package_id,
        bpcr_doc_id=bpcr_id,
        rm_doc_id=rm_id,
        bpcr_weight_kg=10.05,
        rm_weight_kg=10.0,
        operator_signature="A. Operator",
    )

    report = run_service.start_run(
        StartRunSpec(package_id=package_id, rules_dir=pilot_rules_dir)
    )

    assert report.status == RunStatus.COMPLETED
    assert report.stage == RunStage.REPORT
    assert report.rules_evaluated == 3

    weight = _finding_for_rule(
        report, "alcoa.accurate.bpcr-raw-material-weight-match"
    )
    assert len(weight) == 1
    assert weight[0].status == FindingStatus.PASS
    assert weight[0].tolerance_applied == {"kind": "absolute", "value": 0.1, "unit": "kg"}
    assert {e.doc_id for e in weight[0].evidence} == {bpcr_id, rm_id}

    sig = _finding_for_rule(
        report, "alcoa.attributable.operator-signature-present"
    )
    assert sig == []  # signature present on the only bpcr_step_page


def test_graph_emits_fail_for_out_of_tolerance_weight(
    run_service: BMRRunService,
    ingest_service: PackageIngestService,
    package_store: PackageStore,
    pilot_rules_dir: Path,
):
    package_id, bpcr_id, rm_id = build_classified_package(ingest_service)
    write_extraction_fixture(
        package_store,
        package_id,
        bpcr_doc_id=bpcr_id,
        rm_doc_id=rm_id,
        bpcr_weight_kg=10.5,
        rm_weight_kg=10.0,
        operator_signature=None,
    )

    report = run_service.start_run(
        StartRunSpec(package_id=package_id, rules_dir=pilot_rules_dir)
    )

    assert report.status == RunStatus.COMPLETED
    assert report.rules_evaluated == 3

    weight = _finding_for_rule(
        report, "alcoa.accurate.bpcr-raw-material-weight-match"
    )
    assert len(weight) == 1
    assert weight[0].status == FindingStatus.OPEN  # out-of-tolerance -> OPEN
    assert weight[0].severity == "major"

    sig = _finding_for_rule(
        report, "alcoa.attributable.operator-signature-present"
    )
    assert len(sig) == 1
    assert sig[0].status == FindingStatus.OPEN
    assert sig[0].severity == "critical"
    assert sig[0].evidence  # must be anchored per Constitution V
    assert sig[0].evidence[0].doc_id == bpcr_id


def test_graph_fails_cleanly_on_missing_package(
    run_service: BMRRunService,
    pilot_rules_dir: Path,
):
    report = run_service.start_run(
        StartRunSpec(package_id="does-not-exist", rules_dir=pilot_rules_dir)
    )
    assert report.status == RunStatus.FAILED
    assert report.stage == RunStage.INGEST or report.stage == RunStage.REPORT
    assert "not found" in (report.error or "").lower()
    assert report.findings == []


def test_graph_reports_missing_rules_dir(
    run_service: BMRRunService,
    ingest_service: PackageIngestService,
    package_store: PackageStore,
    tmp_path: Path,
):
    package_id, bpcr_id, rm_id = build_classified_package(ingest_service)
    write_extraction_fixture(
        package_store,
        package_id,
        bpcr_doc_id=bpcr_id,
        rm_doc_id=rm_id,
        bpcr_weight_kg=1.0,
        rm_weight_kg=1.0,
        operator_signature="ok",
    )

    report = run_service.start_run(
        StartRunSpec(
            package_id=package_id,
            rules_dir=tmp_path / "nope",
        )
    )
    assert report.status == RunStatus.FAILED
    assert "rules_dir" in (report.error or "")


def test_run_report_is_persisted(
    run_service: BMRRunService,
    ingest_service: PackageIngestService,
    package_store: PackageStore,
    pilot_rules_dir: Path,
):
    package_id, bpcr_id, rm_id = build_classified_package(ingest_service)
    write_extraction_fixture(
        package_store,
        package_id,
        bpcr_doc_id=bpcr_id,
        rm_doc_id=rm_id,
        bpcr_weight_kg=10.0,
        rm_weight_kg=10.0,
        operator_signature="op",
    )

    report = run_service.start_run(
        StartRunSpec(package_id=package_id, rules_dir=pilot_rules_dir)
    )

    reloaded = run_service.get_report(report.run_id)
    assert reloaded is not None
    assert reloaded.run_id == report.run_id
    assert reloaded.status == RunStatus.COMPLETED
    assert reloaded.rules_evaluated == 3
    assert run_service.list_run_ids() == [report.run_id]
