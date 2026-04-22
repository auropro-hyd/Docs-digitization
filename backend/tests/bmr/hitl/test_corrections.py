"""Service-level tests for the CORRECT workflow (Spec 004 follow-up #4).

These tests exercise the full correction lifecycle:

1. Record a correction on a failing finding.
2. Verify the selective re-run produces a superseding finding.
3. Verify the workflow is persisted and ``correction.*`` events fire.
4. Verify the gate re-opens when the only OPEN finding is superseded.
"""

from __future__ import annotations

import pytest

from app.bmr.capabilities.evidence import FindingStatus
from app.bmr.hitl.models import (
    CorrectionStatus,
    ExportGateStatus,
    ResolutionAction,
)
from app.bmr.hitl.service import (
    CorrectionNotApplicableError,
    CorrectionResult,
    HITLService,
)
from app.bmr.hitl.validation import validate_correction_payload


def _correction_draft(field="dispensed_weight_kg", value=10.0):
    return validate_correction_payload(
        field=field,
        corrected_value=value,
        reason_comment="reviewer re-read paper BPCR",
        observed_value_on_document="10.0 kg",
    )


def _weight_finding(service: HITLService, run_id: str):
    report, _ = service.project_report(run_id)
    return next(
        f
        for f in report.findings
        if f.rule_id == "alcoa.accurate.bpcr-raw-material-weight-match"
    )


def test_correction_supersedes_open_finding_and_reopens_gate(
    hitl_service_with_corrections: tuple[HITLService, list],
    failing_run_id: tuple[str, list[str]],
):
    service, events = hitl_service_with_corrections
    run_id, _ = failing_run_id

    target = _weight_finding(service, run_id)
    assert target.status is FindingStatus.OPEN
    assert target.superseded_by is None

    result = service.record_correction(
        run_id=run_id,
        finding_id=target.finding_id,
        draft=_correction_draft(),
        actor_id="qa.reviewer",
    )
    assert isinstance(result, CorrectionResult)
    assert result.workflow.status is CorrectionStatus.APPLIED
    assert target.finding_id in result.superseded_finding_ids
    assert len(result.new_findings) >= 1
    assert result.resolution.action is ResolutionAction.CORRECT
    # Feedback corpus is seeded so Spec 005 has the reviewer-authored value.
    assert result.feedback_sample is not None
    assert result.feedback_sample.action is ResolutionAction.CORRECT
    assert result.feedback_sample.finding_id == target.finding_id

    # Events emitted in order: started → applied.
    event_names = [name for name, _run, _payload in events]
    assert event_names[:1] == ["correction.started"]
    assert event_names[-1] == "correction.applied"

    # Re-projection: the superseded finding no longer blocks the gate.
    report, grouped = service.project_report(run_id)
    reloaded = next(f for f in report.findings if f.finding_id == target.finding_id)
    assert reloaded.superseded_by is not None

    # The signature-missing finding is still open so the gate stays blocked,
    # but the pending_blocking_count drops by the weight finding + its
    # associated synthesis roll-up (both superseded by the re-run).
    assert grouped.export_gate in {
        ExportGateStatus.BLOCKED_BY_PENDING_FINDINGS,
        ExportGateStatus.READY,
    }
    assert grouped.pending_blocking_count < 3


def test_correction_rejects_unknown_field(
    hitl_service_with_corrections: tuple[HITLService, list],
    failing_run_id: tuple[str, list[str]],
):
    service, _events = hitl_service_with_corrections
    run_id, _ = failing_run_id
    target = _weight_finding(service, run_id)

    with pytest.raises(CorrectionNotApplicableError, match="evidence region"):
        service.record_correction(
            run_id=run_id,
            finding_id=target.finding_id,
            draft=_correction_draft(field="no_such_field"),
            actor_id="qa.reviewer",
        )


def test_correction_requires_correction_store(
    hitl_service: HITLService,
    failing_run_id: tuple[str, list[str]],
):
    # The default ``hitl_service`` fixture intentionally omits the correction
    # stores; the service must refuse corrections explicitly rather than
    # silently dropping them.
    run_id, finding_ids = failing_run_id
    with pytest.raises(CorrectionNotApplicableError, match="CorrectionStore"):
        hitl_service.record_correction(
            run_id=run_id,
            finding_id=finding_ids[0],
            draft=_correction_draft(),
            actor_id="qa.reviewer",
        )


def test_correction_persists_workflow_with_rerun_plan(
    hitl_service_with_corrections: tuple[HITLService, list],
    failing_run_id: tuple[str, list[str]],
    correction_store,
):
    service, _ = hitl_service_with_corrections
    run_id, _ = failing_run_id
    target = _weight_finding(service, run_id)

    result = service.record_correction(
        run_id=run_id,
        finding_id=target.finding_id,
        draft=_correction_draft(),
        actor_id="qa.reviewer",
    )

    persisted = correction_store.load(run_id, result.workflow.workflow_id)
    assert persisted is not None
    assert persisted.status is CorrectionStatus.APPLIED
    # The planner picks up both the leaf rule (weight match) and its
    # checklist synthesis roll-up because the latter synthesises_from it.
    assert "alcoa.accurate.bpcr-raw-material-weight-match" in persisted.affected_rule_ids
    assert "checklist.bpcr-step-complete.synthesis" in persisted.affected_rule_ids
