"""End-to-end tests for the HITL service (resolutions → projection → export)."""

from __future__ import annotations

import pytest

from app.bmr.hitl.models import (
    ExportGateStatus,
    GroupKind,
    ResolutionAction,
    SubSectionKind,
)
from app.bmr.hitl.service import (
    ExportGateBlockedError,
    FindingNotFoundError,
    HITLService,
    RunNotFoundError,
)
from app.bmr.hitl.validation import validate_resolution_payload


def _dismiss_draft(reason_type="OCR_MISREAD", observed="12.7 kg"):
    return validate_resolution_payload(
        action="DISMISS",
        reason_type=reason_type,
        observed_value_on_document=observed,
        reason_comment="OCR misread the 7",
        duplicate_of_finding_id=None,
    )


def _confirm_draft():
    return validate_resolution_payload(
        action="CONFIRM",
        reason_type=None,
        observed_value_on_document=None,
        reason_comment="sighted on paper",
        duplicate_of_finding_id=None,
    )


def test_unknown_run_raises(hitl_service: HITLService):
    with pytest.raises(RunNotFoundError):
        hitl_service.record_resolution(
            run_id="nope",
            finding_id="x",
            draft=_confirm_draft(),
            actor_id="tester",
        )
    with pytest.raises(RunNotFoundError):
        hitl_service.project_report("nope")


def test_unknown_finding_raises(
    hitl_service: HITLService, failing_run_id: tuple[str, list[str]]
):
    run_id, _ = failing_run_id
    with pytest.raises(FindingNotFoundError):
        hitl_service.record_resolution(
            run_id=run_id,
            finding_id="deadbeef",
            draft=_confirm_draft(),
            actor_id="tester",
        )


def test_projection_initially_blocked_by_pending(
    hitl_service: HITLService, failing_run_id: tuple[str, list[str]]
):
    run_id, finding_ids = failing_run_id
    _, grouped = hitl_service.project_report(run_id)
    assert grouped.export_gate is ExportGateStatus.BLOCKED_BY_PENDING_FINDINGS
    # The pilot bank now also contains the page_aggregate sample which
    # falls back to UNEVALUATED on this fixture (no BMR page with
    # batch_target_weight_kg). Only blocking-status findings count
    # toward the gate's pending count, so the pending count is a
    # subset of the finding total — strictly positive, never larger.
    assert 1 <= grouped.pending_blocking_count <= len(finding_ids)
    assert grouped.flat_finding_ids == finding_ids
    kinds = {s.group_kind for s in grouped.sections}
    assert GroupKind.BPCR_STEP in kinds


def test_dismiss_creates_feedback_sample_and_unblocks(
    hitl_service: HITLService, failing_run_id: tuple[str, list[str]]
):
    run_id, finding_ids = failing_run_id
    for fid in finding_ids:
        result = hitl_service.record_resolution(
            run_id=run_id,
            finding_id=fid,
            draft=_dismiss_draft(),
            actor_id="tester",
        )
        assert result.resolution.action is ResolutionAction.DISMISS
        assert result.feedback_sample is not None
        assert result.feedback_sample.rule_id
        assert result.feedback_sample.input_context_digest.startswith("sha256:")

    _, grouped = hitl_service.project_report(run_id)
    assert grouped.export_gate is ExportGateStatus.READY
    assert grouped.pending_blocking_count == 0
    assert all(s.all_actioned for s in grouped.sections)


def test_confirm_does_not_seed_feedback(
    hitl_service: HITLService, failing_run_id: tuple[str, list[str]]
):
    run_id, finding_ids = failing_run_id
    result = hitl_service.record_resolution(
        run_id=run_id,
        finding_id=finding_ids[0],
        draft=_confirm_draft(),
        actor_id="tester",
    )
    assert result.feedback_sample is None
    assert result.resolution.action is ResolutionAction.CONFIRM


def test_export_blocked_raises(
    hitl_service: HITLService, failing_run_id: tuple[str, list[str]]
):
    run_id, _ = failing_run_id
    with pytest.raises(ExportGateBlockedError) as exc:
        hitl_service.export_report(run_id, actor_id="tester")
    assert exc.value.status is ExportGateStatus.BLOCKED_BY_PENDING_FINDINGS
    assert exc.value.pending >= 1


def test_export_succeeds_when_all_actioned(
    hitl_service: HITLService, failing_run_id: tuple[str, list[str]]
):
    run_id, finding_ids = failing_run_id
    for fid in finding_ids:
        hitl_service.record_resolution(
            run_id=run_id,
            finding_id=fid,
            draft=_dismiss_draft(),
            actor_id="tester",
        )

    result = hitl_service.export_report(run_id, actor_id="tester")
    assert result.revision.revision_number == 1
    assert result.revision.predecessor_id is None
    assert result.revision.pdf_sha256
    assert result.revision.bundle_sha256
    assert result.pdf_bytes.startswith(b"%PDF-STUB")
    assert b'"run_id"' in result.bundle_bytes


def test_export_revisions_chain(
    hitl_service: HITLService, failing_run_id: tuple[str, list[str]]
):
    run_id, finding_ids = failing_run_id
    for fid in finding_ids:
        hitl_service.record_resolution(
            run_id=run_id,
            finding_id=fid,
            draft=_dismiss_draft(),
            actor_id="tester",
        )
    first = hitl_service.export_report(run_id, actor_id="tester").revision
    second = hitl_service.export_report(run_id, actor_id="tester").revision
    assert second.revision_number == 2
    assert second.predecessor_id == first.revision_id
    assert first.pdf_sha256 == second.pdf_sha256  # deterministic renderer


def test_clean_run_projection_is_ready_without_open_findings(
    hitl_service: HITLService, clean_run_id: str
):
    run_report, grouped = hitl_service.project_report(clean_run_id)
    statuses = {f.status.value for f in run_report.findings}
    assert "open" not in statuses  # only PASS findings on a clean run
    assert grouped.pending_blocking_count == 0
    assert grouped.export_gate is ExportGateStatus.READY


def test_revision_snapshot_links_active_resolution_ids(
    hitl_service: HITLService, failing_run_id: tuple[str, list[str]]
):
    run_id, finding_ids = failing_run_id
    resolution_ids: dict[str, str] = {}
    for fid in finding_ids:
        result = hitl_service.record_resolution(
            run_id=run_id,
            finding_id=fid,
            draft=_dismiss_draft(),
            actor_id="tester",
        )
        resolution_ids[fid] = result.resolution.resolution_id

    revision = hitl_service.export_report(run_id, actor_id="tester").revision
    snapshots_by_finding = {
        s["finding_id"]: s for s in revision.findings_snapshot
    }
    for fid, rid in resolution_ids.items():
        assert snapshots_by_finding[fid]["active_resolution_id"] == rid
        assert snapshots_by_finding[fid]["rule_id"]
        assert snapshots_by_finding[fid]["source"]


def test_projection_populates_subsection_kinds(
    hitl_service: HITLService, failing_run_id: tuple[str, list[str]]
):
    run_id, _ = failing_run_id
    _, grouped = hitl_service.project_report(run_id)
    sub_kinds = {
        sub.kind
        for section in grouped.sections
        for sub in section.sub_sections
        if sub.finding_ids
    }
    # Pilot rules are ALCOA-tagged -> ALCOA sub-section present.
    assert SubSectionKind.ALCOA in sub_kinds
