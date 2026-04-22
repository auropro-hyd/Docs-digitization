"""HTTP smoke tests for the BMR HITL routes (Spec 004)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import bmr_hitl, bmr_packages, bmr_runs
from app.bmr.hitl.service import HITLService
from app.bmr.hitl.stores import FeedbackStore, ResolutionStore, RevisionStore
from app.bmr.ingest.package_store import PackageStore
from app.bmr.ingest.service import PackageIngestService
from app.bmr.workflow.run_store import RunStore
from app.bmr.workflow.service import BMRRunService
from app.main import create_app
from tests.bmr.hitl.conftest import PILOT_RULES_DIR, StubRenderer
from tests.bmr.workflow.conftest import (
    PILOT_MANIFESTS,
    REPO_ROOT,
    write_extraction_fixture,
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    bmr_packages._service.cache_clear()
    bmr_runs._service.cache_clear()
    bmr_hitl._service.cache_clear()

    package_store = PackageStore(tmp_path / "packages")
    run_store = RunStore(tmp_path / "runs")
    hitl_base = tmp_path / "hitl"
    resolution_store = ResolutionStore(hitl_base)
    feedback_store = FeedbackStore(hitl_base)
    revision_store = RevisionStore(hitl_base)

    ingest_service = PackageIngestService(
        store=package_store, manifests_dir=PILOT_MANIFESTS
    )
    run_service = BMRRunService(
        package_store=package_store, run_store=run_store, repo_root=REPO_ROOT
    )
    hitl_service = HITLService(
        run_store=run_store,
        resolution_store=resolution_store,
        feedback_store=feedback_store,
        revision_store=revision_store,
        renderer=StubRenderer(),
    )

    monkeypatch.setattr(bmr_packages, "_service", lambda: ingest_service)
    monkeypatch.setattr(bmr_packages, "_store", lambda: package_store)
    monkeypatch.setattr(bmr_packages, "_manifests_dir", lambda: PILOT_MANIFESTS)
    monkeypatch.setattr(bmr_runs, "_service", lambda: run_service)
    monkeypatch.setattr(bmr_hitl, "_service", lambda: hitl_service)

    app = create_app()
    client = TestClient(app)
    client.package_store = package_store  # type: ignore[attr-defined]
    return client


def _seed_run(client: TestClient, *, violate: bool) -> str:
    files = [
        ("files", ("batch42_bmr.pdf", b"%PDF-1.4 stub", "application/pdf")),
        ("files", ("batch42_bpcr.pdf", b"%PDF-1.4 stub", "application/pdf")),
        ("files", ("raw_material_lactose.pdf", b"%PDF-1.4 stub", "application/pdf")),
    ]
    resp = client.post(
        "/api/bmr/packages", files=files, data={"manifest_id": "default"}
    )
    payload = resp.json()
    bpcr = next(d for d in payload["documents"] if d["role"] == "BPCR")
    rm = next(d for d in payload["documents"] if d["role"] == "RawMaterialPage")
    write_extraction_fixture(
        client.package_store,  # type: ignore[attr-defined]
        payload["package_id"],
        bpcr_doc_id=bpcr["doc_id"],
        rm_doc_id=rm["doc_id"],
        bpcr_weight_kg=10.5 if violate else 10.0,
        rm_weight_kg=10.0,
        operator_signature=None if violate else "op",
    )
    run_resp = client.post(
        "/api/bmr/runs",
        json={"package_id": payload["package_id"], "rules_dir": str(PILOT_RULES_DIR)},
    )
    return run_resp.json()["run_id"]


def test_report_endpoint_returns_grouped_projection(client: TestClient):
    run_id = _seed_run(client, violate=True)
    resp = client.get(f"/api/bmr/runs/{run_id}/report")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["export_gate"] == "blocked_by_pending_findings"
    assert body["pending_blocking_count"] >= 1
    assert len(body["sections"]) >= 1


def test_report_endpoint_unknown_run_404(client: TestClient):
    resp = client.get("/api/bmr/runs/nope/report")
    assert resp.status_code == 404


def test_finding_detail_endpoint(client: TestClient):
    run_id = _seed_run(client, violate=True)
    run = client.get(f"/api/bmr/runs/{run_id}").json()
    finding_id = run["findings"][0]["finding_id"]
    resp = client.get(f"/api/bmr/runs/{run_id}/findings/{finding_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["finding"]["finding_id"] == finding_id
    assert body["current_resolution"] is None


def test_finding_detail_unknown_finding_404(client: TestClient):
    run_id = _seed_run(client, violate=True)
    resp = client.get(f"/api/bmr/runs/{run_id}/findings/deadbeef")
    assert resp.status_code == 404


def test_confirm_resolution_flow(client: TestClient):
    run_id = _seed_run(client, violate=True)
    run = client.get(f"/api/bmr/runs/{run_id}").json()
    finding_id = run["findings"][0]["finding_id"]

    resp = client.post(
        f"/api/bmr/runs/{run_id}/findings/{finding_id}/resolutions",
        json={"action": "CONFIRM", "note": "sighted"},
        headers={"X-Actor-Id": "qa.lead"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["resolution"]["action"] == "CONFIRM"
    assert body["resolution"]["actor_id"] == "qa.lead"
    assert body["feedback_sample_id"] is None

    detail = client.get(f"/api/bmr/runs/{run_id}/findings/{finding_id}").json()
    assert detail["current_resolution"]["resolution_id"] == body["resolution"]["resolution_id"]


def test_dismiss_requires_reason_type_422(client: TestClient):
    run_id = _seed_run(client, violate=True)
    run = client.get(f"/api/bmr/runs/{run_id}").json()
    finding_id = run["findings"][0]["finding_id"]
    resp = client.post(
        f"/api/bmr/runs/{run_id}/findings/{finding_id}/resolutions",
        json={"action": "DISMISS"},
    )
    assert resp.status_code == 422


def test_dismiss_ocr_misread_creates_feedback_sample(client: TestClient):
    run_id = _seed_run(client, violate=True)
    run = client.get(f"/api/bmr/runs/{run_id}").json()
    findings = run["findings"]
    for f in findings:
        resp = client.post(
            f"/api/bmr/runs/{run_id}/findings/{f['finding_id']}/resolutions",
            json={
                "action": "DISMISS",
                "reason_type": "OCR_MISREAD",
                "observed_value_on_document": "10.0 kg",
                "reason_comment": "scan noise",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["feedback_sample_id"] is not None

    gate = client.get(f"/api/bmr/runs/{run_id}/export-gate").json()
    assert gate["status"] == "ready"

    samples = client.get(
        "/api/bmr/feedback/samples", params={"run_id": run_id}
    ).json()
    assert len(samples["items"]) == len(findings)


def test_correct_returns_501(client: TestClient):
    run_id = _seed_run(client, violate=True)
    run = client.get(f"/api/bmr/runs/{run_id}").json()
    finding_id = run["findings"][0]["finding_id"]
    resp = client.post(
        f"/api/bmr/runs/{run_id}/findings/{finding_id}/resolutions",
        json={"action": "CORRECT"},
    )
    assert resp.status_code == 501


def test_export_blocked_returns_409(client: TestClient):
    run_id = _seed_run(client, violate=True)
    resp = client.post(f"/api/bmr/runs/{run_id}/reports:export")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "export_blocked"


def test_export_succeeds_and_serves_pdf_and_bundle(client: TestClient):
    run_id = _seed_run(client, violate=True)
    run = client.get(f"/api/bmr/runs/{run_id}").json()
    for f in run["findings"]:
        client.post(
            f"/api/bmr/runs/{run_id}/findings/{f['finding_id']}/resolutions",
            json={
                "action": "DISMISS",
                "reason_type": "OUT_OF_SCOPE",
                "reason_comment": "not in audit scope",
            },
        )

    export_resp = client.post(f"/api/bmr/runs/{run_id}/reports:export")
    assert export_resp.status_code == 200, export_resp.text
    body = export_resp.json()
    assert body["pdf_url"].startswith("/api/bmr/reports/revisions/")
    revision_id = body["revision"]["revision_id"]

    pdf = client.get(f"/api/bmr/reports/revisions/{revision_id}/pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-STUB")

    bundle = client.get(f"/api/bmr/reports/revisions/{revision_id}/bundle")
    assert bundle.status_code == 200
    assert bundle.json()["run"]["run_id"] == run_id


def test_unknown_revision_returns_404(client: TestClient):
    resp = client.get("/api/bmr/reports/revisions/deadbeef/pdf")
    assert resp.status_code == 404
    resp = client.get("/api/bmr/reports/revisions/deadbeef/bundle")
    assert resp.status_code == 404


def test_feedback_samples_empty_by_default(client: TestClient):
    resp = client.get("/api/bmr/feedback/samples")
    assert resp.status_code == 200
    assert resp.json() == {"items": []}
