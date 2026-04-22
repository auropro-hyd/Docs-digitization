"""HTTP smoke tests for ``/api/bmr/packages``."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import bmr_packages
from app.bmr.ingest.package_store import PackageStore
from app.bmr.ingest.service import PackageIngestService
from app.main import create_app

PILOT_MANIFESTS = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "bmr"
    / "pilot"
    / "manifests"
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    bmr_packages._service.cache_clear()
    store = PackageStore(tmp_path / "packages")
    service = PackageIngestService(store=store, manifests_dir=PILOT_MANIFESTS)

    monkeypatch.setattr(bmr_packages, "_service", lambda: service)
    monkeypatch.setattr(bmr_packages, "_store", lambda: store)
    monkeypatch.setattr(
        bmr_packages, "_manifests_dir", lambda: PILOT_MANIFESTS
    )

    app = create_app()
    return TestClient(app)


def test_manifests_list(client: TestClient):
    resp = client.get("/api/bmr/manifests")
    assert resp.status_code == 200
    ids = {m["id"] for m in resp.json()["manifests"]}
    assert "default" in ids


def test_upload_package_happy_path(client: TestClient):
    files = [
        ("files", ("batch42_bmr.pdf", b"%PDF-1.4 stub", "application/pdf")),
        ("files", ("batch42_bpcr.pdf", b"%PDF-1.4 stub", "application/pdf")),
        ("files", ("raw_material.pdf", b"%PDF-1.4 stub", "application/pdf")),
    ]
    resp = client.post(
        "/api/bmr/packages",
        files=files,
        data={"manifest_id": "default"},
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["status"] == "classified"
    roles = {d["role"] for d in payload["documents"]}
    assert {"BMR", "BPCR", "RawMaterialPage"} <= roles


def test_upload_missing_manifest_rejects(client: TestClient):
    files = [("files", ("bmr.pdf", b"%PDF-1.4", "application/pdf"))]
    resp = client.post(
        "/api/bmr/packages",
        files=files,
        data={"manifest_id": "does-not-exist"},
    )
    assert resp.status_code == 201  # endpoint returns the package even when rejected
    payload = resp.json()
    assert payload["status"] == "rejected"
    assert any(i["kind"] == "manifest_not_found" for i in payload["issues"])


def test_get_package_roundtrip(client: TestClient):
    files = [
        ("files", ("bmr.pdf", b"%PDF-1.4", "application/pdf")),
        ("files", ("bpcr.pdf", b"%PDF-1.4", "application/pdf")),
        ("files", ("raw_material.pdf", b"%PDF-1.4", "application/pdf")),
    ]
    created = client.post(
        "/api/bmr/packages", files=files, data={"manifest_id": "default"}
    ).json()

    fetched = client.get(f"/api/bmr/packages/{created['package_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["package_id"] == created["package_id"]


def test_get_unknown_package_404(client: TestClient):
    resp = client.get("/api/bmr/packages/deadbeef")
    assert resp.status_code == 404


def test_no_files_returns_400(client: TestClient):
    resp = client.post("/api/bmr/packages", data={"manifest_id": "default"})
    # FastAPI enforces File(...) required => 422
    assert resp.status_code in {400, 422}
