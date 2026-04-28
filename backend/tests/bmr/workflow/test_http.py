"""HTTP smoke tests for ``/api/bmr/runs``."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import bmr_packages, bmr_runs
from app.bmr.ingest.package_store import PackageStore
from app.bmr.ingest.service import PackageIngestService
from app.bmr.workflow.run_store import RunStore
from app.bmr.workflow.service import BMRRunService
from app.main import create_app
from tests.bmr.workflow.conftest import write_extraction_fixture

BACKEND_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = Path(__file__).resolve().parents[4]
PILOT_MANIFESTS = BACKEND_ROOT / "config" / "bmr" / "pilot" / "manifests"
PILOT_RULES_DIR = BACKEND_ROOT / "config" / "rules" / "pilot" / "bank"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    bmr_packages._service.cache_clear()
    bmr_runs._service.cache_clear()

    package_store = PackageStore(tmp_path / "packages")
    run_store = RunStore(tmp_path / "runs")
    ingest_service = PackageIngestService(
        store=package_store, manifests_dir=PILOT_MANIFESTS
    )
    run_service = BMRRunService(
        package_store=package_store, run_store=run_store, repo_root=REPO_ROOT
    )

    monkeypatch.setattr(bmr_packages, "_service", lambda: ingest_service)
    monkeypatch.setattr(bmr_packages, "_store", lambda: package_store)
    monkeypatch.setattr(bmr_packages, "_manifests_dir", lambda: PILOT_MANIFESTS)
    monkeypatch.setattr(bmr_runs, "_service", lambda: run_service)

    app = create_app()
    client = TestClient(app)
    client.headers.update({"X-Actor-Id": "test.actor"})
    # Expose helpers the tests need
    client.package_store = package_store  # type: ignore[attr-defined]
    return client


def _upload_and_seed_extraction(
    client: TestClient,
    *,
    bpcr_weight: float,
    rm_weight: float,
    operator_signature: str | None,
) -> str:
    files = [
        ("files", ("batch42_bmr.pdf", b"%PDF-1.4 stub", "application/pdf")),
        ("files", ("batch42_bpcr.pdf", b"%PDF-1.4 stub", "application/pdf")),
        ("files", ("raw_material_lactose.pdf", b"%PDF-1.4 stub", "application/pdf")),
    ]
    resp = client.post(
        "/api/bmr/packages",
        files=files,
        data={"manifest_id": "default"},
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    bpcr = next(d for d in payload["documents"] if d["role"] == "BPCR")
    rm = next(d for d in payload["documents"] if d["role"] == "RawMaterialPage")

    write_extraction_fixture(
        client.package_store,  # type: ignore[attr-defined]
        payload["package_id"],
        bpcr_doc_id=bpcr["doc_id"],
        rm_doc_id=rm["doc_id"],
        bpcr_weight_kg=bpcr_weight,
        rm_weight_kg=rm_weight,
        operator_signature=operator_signature,
    )
    return payload["package_id"]


def test_start_run_and_fetch_report(client: TestClient):
    package_id = _upload_and_seed_extraction(
        client, bpcr_weight=10.0, rm_weight=10.0, operator_signature="op"
    )

    resp = client.post(
        "/api/bmr/runs",
        json={"package_id": package_id, "rules_dir": str(PILOT_RULES_DIR)},
    )
    assert resp.status_code == 201, resp.text
    report = resp.json()
    assert report["status"] == "completed"
    assert report["rules_evaluated"] == 4
    assert report["package_id"] == package_id

    fetched = client.get(f"/api/bmr/runs/{report['run_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["run_id"] == report["run_id"]


def test_start_run_emits_findings_on_out_of_tolerance(client: TestClient):
    package_id = _upload_and_seed_extraction(
        client, bpcr_weight=10.5, rm_weight=10.0, operator_signature=None
    )

    resp = client.post(
        "/api/bmr/runs",
        json={"package_id": package_id, "rules_dir": str(PILOT_RULES_DIR)},
    )
    assert resp.status_code == 201, resp.text
    report = resp.json()
    assert report["status"] == "completed"
    statuses = {f["status"] for f in report["findings"]}
    assert "open" in statuses
    rule_ids = {f["rule_id"] for f in report["findings"]}
    assert "alcoa.accurate.bpcr-raw-material-weight-match" in rule_ids
    assert "alcoa.attributable.operator-signature-present" in rule_ids


def test_start_run_unknown_package_returns_failed_report(client: TestClient):
    resp = client.post(
        "/api/bmr/runs",
        json={
            "package_id": "deadbeef",
            "rules_dir": str(PILOT_RULES_DIR),
        },
    )
    assert resp.status_code == 201
    report = resp.json()
    assert report["status"] == "failed"
    assert "not found" in (report.get("error") or "").lower()


def test_get_unknown_run_returns_404(client: TestClient):
    resp = client.get("/api/bmr/runs/does-not-exist")
    assert resp.status_code == 404


def test_list_runs_is_sorted_and_empty_by_default(client: TestClient):
    resp = client.get("/api/bmr/runs")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


def _force_needs_review(client: TestClient, package_id: str) -> None:
    from app.bmr.ingest.models import PackageIssue, PackageIssueKind, PackageStatus

    store: PackageStore = client.package_store  # type: ignore[attr-defined]
    pkg = store.load(package_id)
    assert pkg is not None
    pkg.status = PackageStatus.NEEDS_REVIEW
    pkg.issues = [
        *pkg.issues,
        PackageIssue(
            kind=PackageIssueKind.UNCLASSIFIED_FILE,
            message="page 2 too blurry for OCR",
            filename="batch42_bpcr.pdf",
        ),
    ]
    store.save(pkg)


def test_legibility_hitl_proceed_round_trip(client: TestClient):
    package_id = _upload_and_seed_extraction(
        client, bpcr_weight=10.0, rm_weight=10.0, operator_signature="op"
    )
    _force_needs_review(client, package_id)

    start = client.post(
        "/api/bmr/runs",
        json={"package_id": package_id, "rules_dir": str(PILOT_RULES_DIR)},
    ).json()
    assert start["status"] == "awaiting_legibility_review"
    assert start["legibility_reasons"]

    status_resp = client.get(f"/api/bmr/runs/{start['run_id']}/legibility")
    assert status_resp.status_code == 200
    status_body = status_resp.json()
    assert status_body["status"] == "awaiting_legibility_review"
    assert "blurry" in " ".join(status_body["reasons"])

    decision = client.post(
        f"/api/bmr/runs/{start['run_id']}/legibility/decision",
        json={"action": "proceed"},
        headers={"X-Actor-Id": "qa.reviewer"},
    )
    assert decision.status_code == 200, decision.text
    resumed = decision.json()
    assert resumed["status"] == "completed"
    assert resumed["legibility_decision"] == "proceed"
    assert resumed["legibility_decided_by"] == "qa.reviewer"


def test_legibility_hitl_reupload_marks_run_failed(client: TestClient):
    package_id = _upload_and_seed_extraction(
        client, bpcr_weight=10.0, rm_weight=10.0, operator_signature="op"
    )
    _force_needs_review(client, package_id)

    start = client.post(
        "/api/bmr/runs",
        json={"package_id": package_id, "rules_dir": str(PILOT_RULES_DIR)},
    ).json()
    assert start["status"] == "awaiting_legibility_review"

    decision = client.post(
        f"/api/bmr/runs/{start['run_id']}/legibility/decision",
        json={
            "action": "reupload",
            "note": "request fresh scan, this one is unreadable",
        },
        headers={"X-Actor-Id": "qa.reviewer"},
    )
    assert decision.status_code == 200
    body = decision.json()
    assert body["status"] == "failed"
    assert body["legibility_decision"] == "reupload"
    assert "unreadable" in body["legibility_decision_note"]


def test_legibility_decision_on_completed_run_conflicts(client: TestClient):
    package_id = _upload_and_seed_extraction(
        client, bpcr_weight=10.0, rm_weight=10.0, operator_signature="op"
    )
    start = client.post(
        "/api/bmr/runs",
        json={"package_id": package_id, "rules_dir": str(PILOT_RULES_DIR)},
    ).json()
    assert start["status"] == "completed"

    resp = client.post(
        f"/api/bmr/runs/{start['run_id']}/legibility/decision",
        json={"action": "proceed"},
    )
    assert resp.status_code == 409
