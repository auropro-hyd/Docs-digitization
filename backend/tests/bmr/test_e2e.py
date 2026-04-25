"""End-to-end quickstart: upload → run → dismiss → export.

This is the single test that proves the v0 vertical slice hangs together
from HTTP through every BMR subsystem:

    ingest (Spec 002) → rules (Spec 003) → pipeline (Spec 001) →
    HITL + report + export (Spec 004)

It uses the StubRenderer so the test suite runs without WeasyPrint's
native deps. The same code path with the default WeasyPrintRenderer is
exercised in the quickstart docs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import bmr_hitl, bmr_packages, bmr_runs
from app.bmr.events import get_event_bus, reset_event_bus
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
    reset_event_bus()
    event_bus = get_event_bus()

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
        package_store=package_store,
        run_store=run_store,
        repo_root=REPO_ROOT,
        event_bus=event_bus,
    )
    correction_store = CorrectionStore(hitl_base)
    correction_events: list[tuple[str, str, dict]] = []
    hitl_service = HITLService(
        run_store=run_store,
        resolution_store=resolution_store,
        feedback_store=feedback_store,
        revision_store=revision_store,
        correction_store=correction_store,
        package_store=package_store,
        renderer=StubRenderer(),
        event_emitter=lambda name, run_id, payload: correction_events.append(
            (name, run_id, payload)
        ),
    )
    monkeypatch.setattr(bmr_packages, "_service", lambda: ingest_service)
    monkeypatch.setattr(bmr_packages, "_store", lambda: package_store)
    monkeypatch.setattr(bmr_packages, "_manifests_dir", lambda: PILOT_MANIFESTS)
    monkeypatch.setattr(bmr_runs, "_service", lambda: run_service)
    monkeypatch.setattr(bmr_runs, "get_event_bus", lambda: event_bus)
    monkeypatch.setattr(bmr_hitl, "_service", lambda: hitl_service)

    app = create_app()
    client = TestClient(app)
    client.headers.update({"X-Actor-Id": "test.actor"})
    client.package_store = package_store  # type: ignore[attr-defined]
    client.correction_events = correction_events  # type: ignore[attr-defined]
    return client


def test_v0_quickstart_end_to_end(client: TestClient):
    # 1. List manifests
    manifests = client.get("/api/bmr/manifests").json()["manifests"]
    assert any(m["id"] == "default" for m in manifests)

    # 2. Upload a BMR package with a deliberate weight mismatch + missing signature
    files = [
        ("files", ("batch42_bmr.pdf", b"%PDF-1.4 stub", "application/pdf")),
        ("files", ("batch42_bpcr.pdf", b"%PDF-1.4 stub", "application/pdf")),
        ("files", ("raw_material_lactose.pdf", b"%PDF-1.4 stub", "application/pdf")),
    ]
    pkg = client.post(
        "/api/bmr/packages", files=files, data={"manifest_id": "default"}
    ).json()
    assert pkg["status"] == "classified"

    bpcr = next(d for d in pkg["documents"] if d["role"] == "BPCR")
    rm = next(d for d in pkg["documents"] if d["role"] == "RawMaterialPage")
    write_extraction_fixture(
        client.package_store,  # type: ignore[attr-defined]
        pkg["package_id"],
        bpcr_doc_id=bpcr["doc_id"],
        rm_doc_id=rm["doc_id"],
        bpcr_weight_kg=10.5,  # ±0.1 kg tolerance violated
        rm_weight_kg=10.0,
        operator_signature=None,  # ALCOA-Attributable violation
    )

    # 3. Start a run — returns the terminal report synchronously for v0.
    run = client.post(
        "/api/bmr/runs",
        json={
            "package_id": pkg["package_id"],
            "rules_dir": str(PILOT_RULES_DIR),
        },
    ).json()
    assert run["status"] == "completed"
    assert run["rules_evaluated"] == 3
    # 2 leaf findings (weight-mismatch OPEN, signature missing OPEN) + 1
    # checklist_synthesis roll-up at BPCR step level.
    assert len(run["findings"]) == 3
    sources = [f["source"] for f in run["findings"]]
    assert sources.count("checklist_synthesis") == 1
    assert sources.count("alcoa") == 2

    # 4. Grouped report is blocked by pending findings.
    report = client.get(f"/api/bmr/runs/{run['run_id']}/report").json()
    assert report["export_gate"] == "blocked_by_pending_findings"
    assert report["pending_blocking_count"] == 3

    gate = client.get(f"/api/bmr/runs/{run['run_id']}/export-gate").json()
    assert gate["status"] == "blocked_by_pending_findings"

    # 5. Export is refused with 409.
    blocked = client.post(f"/api/bmr/runs/{run['run_id']}/reports:export")
    assert blocked.status_code == 409

    # 6. Dismiss both findings with an OCR misread reason.
    for finding in run["findings"]:
        resp = client.post(
            f"/api/bmr/runs/{run['run_id']}/findings/{finding['finding_id']}/resolutions",
            json={
                "action": "DISMISS",
                "reason_type": "OCR_MISREAD",
                "observed_value_on_document": "10.0 kg",
                "reason_comment": "verified against paper batch record",
            },
            headers={"X-Actor-Id": "qa.reviewer"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["feedback_sample_id"] is not None

    # 7. Gate is now ready, export succeeds.
    gate = client.get(f"/api/bmr/runs/{run['run_id']}/export-gate").json()
    assert gate["status"] == "ready"

    export = client.post(f"/api/bmr/runs/{run['run_id']}/reports:export").json()
    rev_id = export["revision"]["revision_id"]
    assert export["revision"]["revision_number"] == 1

    # Follow-up #8: the revision carries an immutable findings_snapshot
    # tagging each finding with the active resolution id used to clear
    # the gate. Reviewers inspecting the export can trace exactly which
    # resolution justified the sign-off.
    snapshot = export["revision"]["findings_snapshot"]
    assert len(snapshot) == 3
    resolution_ids = {row["active_resolution_id"] for row in snapshot}
    assert None not in resolution_ids
    assert all(row["status"] == "open" for row in snapshot)

    pdf = client.get(f"/api/bmr/reports/revisions/{rev_id}/pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-STUB")

    bundle = client.get(f"/api/bmr/reports/revisions/{rev_id}/bundle").json()
    assert bundle["run"]["run_id"] == run["run_id"]
    assert len(bundle["resolutions"]) == 3
    assert len(bundle["feedback_samples"]) == 3

    # 8. Feedback corpus captures leaf rules AND the synthesis roll-up so
    #    Spec 005's authoring skill can tune both strata.
    samples = client.get(
        "/api/bmr/feedback/samples", params={"run_id": run["run_id"]}
    ).json()
    assert len(samples["items"]) == 3
    rule_ids = {s["rule_id"] for s in samples["items"]}
    assert "alcoa.accurate.bpcr-raw-material-weight-match" in rule_ids
    assert "alcoa.attributable.operator-signature-present" in rule_ids
    assert "checklist.bpcr-step-complete.synthesis" in rule_ids

    # 9. CORRECT workflow: reviewer overrides the BPCR dispensed weight, the
    #    cross-doc rule re-runs and supersedes the original OPEN finding with
    #    a clean PASS record. Emits correction.started + correction.applied.
    weight_finding = next(
        f
        for f in run["findings"]
        if f["rule_id"] == "alcoa.accurate.bpcr-raw-material-weight-match"
    )
    correct = client.post(
        f"/api/bmr/runs/{run['run_id']}/findings/{weight_finding['finding_id']}/corrections",
        json={
            "field": "dispensed_weight_kg",
            "corrected_value": 10.0,
            "reason_comment": "re-read from paper BPCR — OCR mis-read 10.5 for 10.0",
            "observed_value_on_document": "10.0 kg",
        },
        headers={"X-Actor-Id": "qa.reviewer"},
    )
    assert correct.status_code == 201, correct.text
    correction_body = correct.json()
    assert correction_body["workflow"]["status"] == "applied"
    assert weight_finding["finding_id"] in correction_body["superseded_finding_ids"]
    assert len(correction_body["new_finding_ids"]) >= 1

    event_names = [evt[0] for evt in client.correction_events]  # type: ignore[attr-defined]
    assert "correction.started" in event_names
    assert "correction.applied" in event_names

    # Re-projecting the report should mark the superseded finding and carry
    # the new clean finding through the summary.
    detail = client.get(
        f"/api/bmr/runs/{run['run_id']}/findings/{weight_finding['finding_id']}"
    ).json()
    assert detail["finding"]["superseded_by"] is not None


def test_v0_quickstart_legibility_hitl_and_events(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Follow-ups #2 (legibility HITL) and #6 (WebSocket events) together.

    Drives a package into NEEDS_REVIEW so the legibility gate fires,
    then walks the reviewer through a ``proceed`` decision and checks
    that lifecycle events streamed over the WebSocket surface every
    transition in order.
    """

    from app.bmr.ingest.models import (
        PackageIssue,
        PackageIssueKind,
        PackageStatus,
    )

    files = [
        ("files", ("batch77_bmr.pdf", b"%PDF-1.4 stub", "application/pdf")),
        ("files", ("batch77_bpcr.pdf", b"%PDF-1.4 stub", "application/pdf")),
        ("files", ("raw_material_lactose.pdf", b"%PDF-1.4 stub", "application/pdf")),
    ]
    pkg = client.post(
        "/api/bmr/packages", files=files, data={"manifest_id": "default"}
    ).json()

    bpcr = next(d for d in pkg["documents"] if d["role"] == "BPCR")
    rm = next(d for d in pkg["documents"] if d["role"] == "RawMaterialPage")
    write_extraction_fixture(
        client.package_store,  # type: ignore[attr-defined]
        pkg["package_id"],
        bpcr_doc_id=bpcr["doc_id"],
        rm_doc_id=rm["doc_id"],
        bpcr_weight_kg=10.0,
        rm_weight_kg=10.0,
        operator_signature="A. Operator",
    )

    # Force NEEDS_REVIEW so the legibility gate fires. In production the
    # classifier or OCR quality scorer sets this automatically; we inject
    # it so the test is hermetic.
    package = client.package_store.load(pkg["package_id"])  # type: ignore[attr-defined]
    package.status = PackageStatus.NEEDS_REVIEW
    package.issues = [
        *package.issues,
        PackageIssue(
            kind=PackageIssueKind.UNCLASSIFIED_FILE,
            message="page 2 illegible — reviewer must confirm",
            filename="batch77_bpcr.pdf",
        ),
    ]
    client.package_store.save(package)  # type: ignore[attr-defined]

    run = client.post(
        "/api/bmr/runs",
        json={
            "package_id": pkg["package_id"],
            "rules_dir": str(PILOT_RULES_DIR),
        },
    ).json()
    assert run["status"] == "awaiting_legibility_review"
    assert run["rules_evaluated"] == 0

    status = client.get(f"/api/bmr/runs/{run['run_id']}/legibility").json()
    assert status["status"] == "awaiting_legibility_review"
    assert any("illegible" in reason for reason in status["reasons"])

    # WebSocket subscribes mid-flight and should receive the resume
    # events (run.legibility_decided, run.started, run.completed).
    with client.websocket_connect(
        f"/api/bmr/runs/{run['run_id']}/events"
    ) as ws:
        snapshot = ws.receive_json()
        assert snapshot["event"] == "snapshot"
        assert snapshot["payload"]["status"] == "awaiting_legibility_review"

        decision = client.post(
            f"/api/bmr/runs/{run['run_id']}/legibility/decision",
            json={"action": "proceed", "note": "reviewer confirmed scan is fine"},
            headers={"X-Actor-Id": "qa.reviewer"},
        )
        assert decision.status_code == 200, decision.text
        assert decision.json()["status"] == "completed"

        seen: list[str] = []
        # Drain until we see the terminal event. The starlette test
        # transport schedules queued envelopes before unblocking
        # ``receive_json``, so this loop will not hang in practice.
        for _ in range(6):
            event = ws.receive_json()
            seen.append(event["event"])
            if event["event"] == "run.completed":
                break

    assert "run.legibility_decided" in seen
    assert "run.completed" in seen
