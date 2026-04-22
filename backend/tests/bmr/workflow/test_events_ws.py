"""Integration test for ``/api/bmr/runs/{run_id}/events`` WebSocket.

The WS endpoint is the v0 surface for live run lifecycle events. These
tests make sure a subscriber that connects before the run starts sees
``run.started → run.completed`` in the expected order.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import bmr_hitl, bmr_packages, bmr_runs
from app.bmr.events import EventBus, reset_event_bus
from app.bmr.ingest.package_store import PackageStore
from app.bmr.ingest.service import PackageIngestService
from app.bmr.workflow.run_store import RunStore
from app.bmr.workflow.service import BMRRunService
from app.main import create_app
from tests.bmr.workflow.conftest import (
    PILOT_RULES_DIR,
    REPO_ROOT,
    build_classified_package,
    write_extraction_fixture,
)


@pytest.fixture
def ws_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bmr_packages._service.cache_clear()
    bmr_runs._service.cache_clear()
    bmr_hitl._service.cache_clear()
    reset_event_bus()

    package_store = PackageStore(tmp_path / "packages")
    run_store = RunStore(tmp_path / "runs")
    ingest_service = PackageIngestService(
        store=package_store,
        manifests_dir=(
            Path(__file__).resolve().parents[3]
            / "config"
            / "bmr"
            / "pilot"
            / "manifests"
        ),
    )
    bus = EventBus()
    run_service = BMRRunService(
        package_store=package_store,
        run_store=run_store,
        repo_root=REPO_ROOT,
        event_bus=bus,
    )

    monkeypatch.setattr(bmr_packages, "_service", lambda: ingest_service)
    monkeypatch.setattr(bmr_packages, "_store", lambda: package_store)
    monkeypatch.setattr(bmr_runs, "_service", lambda: run_service)
    monkeypatch.setattr("app.bmr.events.get_event_bus", lambda: bus)
    monkeypatch.setattr(bmr_runs, "get_event_bus", lambda: bus)

    app = create_app()
    client = TestClient(app)
    client.package_store = package_store  # type: ignore[attr-defined]
    client.ingest_service = ingest_service  # type: ignore[attr-defined]
    return client


def test_run_events_ws_delivers_started_and_completed(ws_client: TestClient):
    package_id, bpcr_id, rm_id = build_classified_package(
        ws_client.ingest_service  # type: ignore[attr-defined]
    )
    write_extraction_fixture(
        ws_client.package_store,  # type: ignore[attr-defined]
        package_id,
        bpcr_doc_id=bpcr_id,
        rm_doc_id=rm_id,
        bpcr_weight_kg=10.0,
        rm_weight_kg=10.0,
        operator_signature="A. Operator",
    )

    # Seed a pending run via the run-store so the WS can subscribe to a
    # known run_id before the graph kicks off. We then start the run in a
    # background thread so the WS has a chance to pick up the events.
    run_id = "preseeded-run-id"
    from app.bmr.workflow.models import RunReport, RunStage, RunStatus, now_utc

    preseed = RunReport(
        run_id=run_id,
        package_id=package_id,
        status=RunStatus.PENDING,
        stage=RunStage.INGEST,
        started_at=now_utc(),
    )
    run_store: RunStore = bmr_runs._service()._run_store  # type: ignore[attr-defined]
    run_store.save(preseed)

    events: list[dict] = []
    with ws_client.websocket_connect(f"/api/bmr/runs/{run_id}/events") as ws:
        # Pull the initial snapshot.
        snapshot = ws.receive_json()
        assert snapshot["event"] == "snapshot"
        assert snapshot["payload"]["status"] == "pending"

        # Now publish some events directly via the bus and assert that
        # the socket relays them. (We avoid racing the full pipeline by
        # testing the bus plumbing here; the service-level emit is
        # already covered by test_events_service.)
        import app.api.routes.bmr_runs as runs_module

        bus = runs_module.get_event_bus()
        bus.publish("run.started", run_id, {"package_id": package_id})
        bus.publish(
            "run.completed",
            run_id,
            {"rules_evaluated": 3, "finding_count": 0},
        )

        events.append(ws.receive_json())
        events.append(ws.receive_json())

    names = [e["event"] for e in events]
    assert names == ["run.started", "run.completed"]
    assert events[0]["payload"]["package_id"] == package_id
    assert events[1]["payload"]["rules_evaluated"] == 3


def test_service_publishes_run_started_and_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """End-to-end: the run service emits the terminal lifecycle events."""

    bmr_packages._service.cache_clear()
    bmr_runs._service.cache_clear()
    bmr_hitl._service.cache_clear()
    reset_event_bus()

    package_store = PackageStore(tmp_path / "packages")
    run_store = RunStore(tmp_path / "runs")
    ingest_service = PackageIngestService(
        store=package_store,
        manifests_dir=(
            Path(__file__).resolve().parents[3]
            / "config"
            / "bmr"
            / "pilot"
            / "manifests"
        ),
    )

    captured: list[dict] = []

    def _spy(event: str, run_id: str, payload=None) -> None:
        captured.append(
            {"event": event, "run_id": run_id, "payload": dict(payload or {})}
        )

    run_service = BMRRunService(
        package_store=package_store,
        run_store=run_store,
        repo_root=REPO_ROOT,
        event_publisher=_spy,
    )

    monkeypatch.setattr(bmr_packages, "_service", lambda: ingest_service)
    monkeypatch.setattr(bmr_packages, "_store", lambda: package_store)
    monkeypatch.setattr(bmr_runs, "_service", lambda: run_service)

    app = create_app()
    client = TestClient(app)

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

    resp = client.post(
        "/api/bmr/runs",
        json={"package_id": package_id, "rules_dir": str(PILOT_RULES_DIR)},
    )
    assert resp.status_code == 201, resp.text

    events = [e["event"] for e in captured]
    assert "run.started" in events
    assert "run.completed" in events
    assert events.index("run.started") < events.index("run.completed")
