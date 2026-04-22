"""Shared fixtures for BMR HITL tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.bmr.hitl.service import HITLService
from app.bmr.hitl.stores import (
    CorrectionStore,
    FeedbackStore,
    ResolutionStore,
    RevisionStore,
)
from app.bmr.ingest.package_store import PackageStore
from app.bmr.ingest.service import PackageIngestService
from app.bmr.workflow.run_store import RunStore
from app.bmr.workflow.service import BMRRunService, StartRunSpec
from tests.bmr.workflow.conftest import (
    BACKEND_ROOT,
    PILOT_MANIFESTS,
    REPO_ROOT,
    build_classified_package,
    write_extraction_fixture,
)

PILOT_RULES_DIR = BACKEND_ROOT / "config" / "rules" / "pilot" / "bank"


@pytest.fixture
def package_store(tmp_path: Path) -> PackageStore:
    return PackageStore(tmp_path / "packages")


@pytest.fixture
def run_store(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path / "runs")


@pytest.fixture
def resolution_store(tmp_path: Path) -> ResolutionStore:
    return ResolutionStore(tmp_path / "hitl")


@pytest.fixture
def feedback_store(tmp_path: Path) -> FeedbackStore:
    return FeedbackStore(tmp_path / "hitl")


@pytest.fixture
def revision_store(tmp_path: Path) -> RevisionStore:
    return RevisionStore(tmp_path / "hitl")


@pytest.fixture
def correction_store(tmp_path: Path) -> CorrectionStore:
    return CorrectionStore(tmp_path / "hitl")


@pytest.fixture
def ingest_service(package_store: PackageStore) -> PackageIngestService:
    return PackageIngestService(store=package_store, manifests_dir=PILOT_MANIFESTS)


@pytest.fixture
def run_service(
    package_store: PackageStore, run_store: RunStore
) -> BMRRunService:
    return BMRRunService(
        package_store=package_store,
        run_store=run_store,
        repo_root=REPO_ROOT,
    )


class StubRenderer:
    """Deterministic renderer used by tests so WeasyPrint's native deps are optional."""

    def render_html(self, *, run_report, grouped_report, resolutions) -> str:
        return (
            f"<html><body>run={run_report.run_id} "
            f"gate={grouped_report.export_gate.value} "
            f"res={len(resolutions)}</body></html>"
        )

    def render_pdf(self, html_body: str) -> bytes:
        return b"%PDF-STUB\n" + html_body.encode("utf-8")


@pytest.fixture
def stub_renderer() -> StubRenderer:
    return StubRenderer()


@pytest.fixture
def hitl_service(
    run_store: RunStore,
    resolution_store: ResolutionStore,
    feedback_store: FeedbackStore,
    revision_store: RevisionStore,
    stub_renderer: StubRenderer,
) -> HITLService:
    return HITLService(
        run_store=run_store,
        resolution_store=resolution_store,
        feedback_store=feedback_store,
        revision_store=revision_store,
        renderer=stub_renderer,
    )


@pytest.fixture
def hitl_service_with_corrections(
    run_store: RunStore,
    resolution_store: ResolutionStore,
    feedback_store: FeedbackStore,
    revision_store: RevisionStore,
    correction_store: CorrectionStore,
    package_store: PackageStore,
    stub_renderer: StubRenderer,
) -> tuple[HITLService, list[tuple[str, str, dict]]]:
    events: list[tuple[str, str, dict]] = []
    service = HITLService(
        run_store=run_store,
        resolution_store=resolution_store,
        feedback_store=feedback_store,
        revision_store=revision_store,
        correction_store=correction_store,
        package_store=package_store,
        renderer=stub_renderer,
        event_emitter=lambda name, run_id, payload: events.append(
            (name, run_id, payload)
        ),
    )
    return service, events


@pytest.fixture
def failing_run_id(
    ingest_service: PackageIngestService,
    package_store: PackageStore,
    run_service: BMRRunService,
) -> tuple[str, list[str]]:
    """Run the pipeline with violations so the report has open findings.

    Returns ``(run_id, finding_ids)``.
    """

    package_id, bpcr_id, rm_id = build_classified_package(ingest_service)
    write_extraction_fixture(
        package_store,
        package_id,
        bpcr_doc_id=bpcr_id,
        rm_doc_id=rm_id,
        bpcr_weight_kg=10.5,
        rm_weight_kg=10.0,
        operator_signature=None,  # triggers same_page OPEN finding
    )
    report = run_service.start_run(
        StartRunSpec(package_id=package_id, rules_dir=PILOT_RULES_DIR)
    )
    return report.run_id, [f.finding_id for f in report.findings]


@pytest.fixture
def clean_run_id(
    ingest_service: PackageIngestService,
    package_store: PackageStore,
    run_service: BMRRunService,
) -> str:
    """Run the pipeline within tolerance so no open findings remain."""

    package_id, bpcr_id, rm_id = build_classified_package(ingest_service)
    write_extraction_fixture(
        package_store,
        package_id,
        bpcr_doc_id=bpcr_id,
        rm_doc_id=rm_id,
        bpcr_weight_kg=10.0,
        rm_weight_kg=10.0,
        operator_signature="A. Operator",
    )
    report = run_service.start_run(
        StartRunSpec(package_id=package_id, rules_dir=PILOT_RULES_DIR)
    )
    return report.run_id
