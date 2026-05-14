"""Integration tests for the segmentation endpoints — Spec 011 / US4.

Pins:
- ``PUT /api/compliance/{doc_id}/segmentation`` writes both the
  full segmentation.json AND the per-field overrides sidecar.
- ``POST /api/compliance/{doc_id}/segment`` re-runs the LLM but
  applies the stored overrides on top, so operator edits survive.
- ``GET /api/compliance/{doc_id}/segmentation`` surfaces
  ``validation_issues`` from the post-process pipeline (FR-014).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import compliance as compliance_route


_DOC_ID = "seg-doc-1"


def _seed_doc(tmp_path: Path) -> Path:
    """Drop result.json + an existing segmentation.json so PUT
    has a baseline to diff against."""

    doc_dir = tmp_path / _DOC_ID
    doc_dir.mkdir(parents=True)
    (doc_dir / "result.json").write_text(
        json.dumps({
            "filename": "seg.pdf",
            "total_pages": 50,
            "extractions": [
                {"page_num": p, "markdown": f"Page {p} of 50\nbody"}
                for p in range(1, 51)
            ],
            "key_value_pairs": [],
        }),
        encoding="utf-8",
    )
    seg = {
        "sections": [
            {
                "section_id": "rm",
                "name": "Raw Material Request",
                "section_type": "material_request",
                "document_type": "raw_material_request",
                "start_page": 1,
                "end_page": 25,
                "description": "",
            },
        ],
        "document_type": "raw_material_request",
        "confidence": 0.9,
        "validation_issues": [],
    }
    (doc_dir / "segmentation.json").write_text(
        json.dumps(seg, indent=2), encoding="utf-8",
    )
    return doc_dir


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    _seed_doc(tmp_path)

    def fake_doc_dir(doc_id: str) -> Path:
        d = tmp_path / doc_id
        if not d.exists():
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
        return d

    monkeypatch.setattr(compliance_route, "_doc_dir", fake_doc_dir)

    from app.main import create_app
    app = create_app()
    client = TestClient(app)
    client.tmp_path = tmp_path  # type: ignore[attr-defined]
    return client


def _put(client: TestClient, body: dict, *, actor: str = "test@auropro.com") -> dict:
    return client.put(
        f"/api/compliance/{_DOC_ID}/segmentation",
        json=body,
        headers={"X-Actor-Id": actor},
    ).json()


# ── PUT /segmentation writes overrides ────────────────────────


def test_put_diffs_and_records_overrides(client: TestClient, tmp_path: Path) -> None:
    """Operator extends end_page 25 → 27; the PUT writes the new
    full segmentation AND appends one override record to the
    sidecar."""

    edited = {
        "sections": [
            {
                "section_id": "rm",
                "name": "Raw Material Request",
                "section_type": "material_request",
                "document_type": "raw_material_request",
                "start_page": 1,
                "end_page": 27,
                "description": "",
            },
        ],
        "document_type": "raw_material_request",
        "confidence": 0.9,
    }
    resp = _put(client, edited)
    assert resp["overrides_recorded"] == 1

    sidecar = tmp_path / _DOC_ID / "segmentation.overrides.json"
    assert sidecar.exists()
    data = json.loads(sidecar.read_text("utf-8"))
    assert len(data) == 1
    assert data[0]["section_id"] == "rm"
    assert data[0]["field"] == "end_page"
    assert data[0]["value"] == 27
    assert data[0]["actor"] == "test@auropro.com"


def test_put_with_no_changes_records_nothing(client: TestClient, tmp_path: Path) -> None:
    """PUT the existing segmentation back unchanged → no overrides
    recorded, no sidecar created."""

    baseline = json.loads(
        (tmp_path / _DOC_ID / "segmentation.json").read_text("utf-8"),
    )
    resp = _put(client, baseline)
    assert resp["overrides_recorded"] == 0
    sidecar = tmp_path / _DOC_ID / "segmentation.overrides.json"
    assert not sidecar.exists()


def test_put_records_actor_defaults_to_unknown(client: TestClient, tmp_path: Path) -> None:
    """No ``X-Actor-Id`` header → recorded as 'unknown' so audit
    trail still works."""

    edited = json.loads(
        (tmp_path / _DOC_ID / "segmentation.json").read_text("utf-8"),
    )
    edited["sections"][0]["end_page"] = 30
    resp = client.put(f"/api/compliance/{_DOC_ID}/segmentation", json=edited)
    body = resp.json()
    assert body["overrides_recorded"] == 1
    sidecar_data = json.loads(
        (tmp_path / _DOC_ID / "segmentation.overrides.json").read_text("utf-8"),
    )
    assert sidecar_data[0]["actor"] == "unknown"


# ── GET surfaces validation_issues ────────────────────────────


def test_get_returns_validation_issues_field(client: TestClient) -> None:
    """The seeded segmentation has the field; GET returns it."""

    resp = client.get(f"/api/compliance/{_DOC_ID}/segmentation")
    body = resp.json()
    assert "validation_issues" in body
    assert isinstance(body["validation_issues"], list)


# ── PUT → POST flow with stub LLM ─────────────────────────────


class _StubLLM:
    """Returns a deterministic fresh segmentation that DIFFERS from
    the operator-edited one (so we can verify the override is
    what survives)."""

    def __init__(self, fresh: dict) -> None:
        self._fresh = fresh

    async def generate(self, *_args, **_kwargs):  # pragma: no cover
        raise NotImplementedError

    async def generate_structured(self, *_args, **_kwargs):
        from app.compliance.models import DocumentSegmentation
        return DocumentSegmentation.model_validate(self._fresh)


def test_post_segment_applies_persisted_override(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end pin: operator extends end_page to 27; re-segment
    runs an LLM that emits end_page=25; the saved
    segmentation.json carries 27."""

    # 1. Operator saves an override extending end_page 25 → 27.
    edited = {
        "sections": [
            {
                "section_id": "rm",
                "name": "Raw Material Request",
                "section_type": "material_request",
                "document_type": "raw_material_request",
                "start_page": 1,
                "end_page": 27,
                "description": "",
            },
        ],
        "document_type": "raw_material_request",
        "confidence": 0.9,
    }
    _put(client, edited)

    # 2. Patch the LLM container so the re-segment call returns a
    # FRESH segmentation that doesn't carry the operator's edit.
    fresh = {
        "sections": [{
            "section_id": "rm",
            "name": "Raw Material Request",
            "section_type": "material_request",
            "document_type": "raw_material_request",
            "start_page": 1,
            "end_page": 25,
            "description": "",
        }],
        "document_type": "raw_material_request",
        "confidence": 0.95,
    }
    stub_llm = _StubLLM(fresh)
    class _StubContainer:
        compliance_cross_page_llm = stub_llm

    from app.config import container as container_module
    monkeypatch.setattr(container_module, "get_container", lambda: _StubContainer())

    # 3. Trigger re-segmentation (the route spawns a task; we wait
    # for it to settle by polling the file).
    resp = client.post(f"/api/compliance/{_DOC_ID}/segment")
    assert resp.status_code == 200

    seg_path = tmp_path / _DOC_ID / "segmentation.json"
    for _ in range(50):
        # Wait up to 5 seconds for the background task to finish.
        if seg_path.exists():
            data = json.loads(seg_path.read_text("utf-8"))
            if data["sections"][0].get("end_page") == 27:
                break
        # Lightweight async tick — TestClient runs sync but the
        # background task is asyncio under the hood.
        import time
        time.sleep(0.1)

    final = json.loads(seg_path.read_text("utf-8"))
    # The override (27) MUST survive the fresh LLM output (25).
    assert final["sections"][0]["end_page"] == 27
