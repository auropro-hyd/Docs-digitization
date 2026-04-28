"""Tests for the legibility HITL interrupt (Spec 004 follow-up #2).

When the ingest/classification stage marks a package ``NEEDS_REVIEW``,
the BMR run must pause at the legibility gate instead of silently
proceeding. A reviewer then either:

- ``proceed``: resume the pipeline (compliance + report run as usual)
- ``reupload``: abort the run with a clear audit trail

These tests assert both paths end-to-end through :class:`BMRRunService`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.bmr.ingest.models import (
    ClassificationDecisionSource,
    DocumentPackage,
    DocumentRef,
    PackageIssue,
    PackageIssueKind,
    PackageStatus,
)
from app.bmr.ingest.package_store import PackageStore
from app.bmr.workflow.models import RunStatus, now_utc
from app.bmr.workflow.service import (
    BMRRunService,
    LegibilityDecisionError,
    StartRunSpec,
)
from tests.bmr.workflow.conftest import (
    PILOT_RULES_DIR,
    build_classified_package,
    write_extraction_fixture,
)


def _mark_package_needs_review(
    package_store: PackageStore, package_id: str, *, extra_issue_message: str
) -> None:
    """Downgrade an already-classified package to NEEDS_REVIEW.

    The ingest service normally sets this when the classifier is unsure
    about a file; we force it here so the legibility gate fires on a
    controlled fixture.
    """

    package = package_store.load(package_id)
    assert package is not None
    package.status = PackageStatus.NEEDS_REVIEW
    package.issues = [
        *package.issues,
        PackageIssue(
            kind=PackageIssueKind.UNCLASSIFIED_FILE,
            message=extra_issue_message,
            filename="mystery.pdf",
        ),
    ]
    package_store.save(package)


@pytest.fixture
def paused_run(
    ingest_service,
    package_store,
    run_service: BMRRunService,
):
    package_id, bpcr_id, rm_id = build_classified_package(ingest_service)
    _mark_package_needs_review(
        package_store, package_id, extra_issue_message="page 3 illegible"
    )
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
    assert report.status is RunStatus.AWAITING_LEGIBILITY_REVIEW
    return report


def test_needs_review_package_pauses_run(paused_run):
    assert paused_run.status is RunStatus.AWAITING_LEGIBILITY_REVIEW
    # Reasons carry the issue so the reviewer knows what to look at.
    assert any(
        "unclassified_file" in reason and "illegible" in reason
        for reason in paused_run.legibility_reasons
    )
    # The graph should NOT have produced any findings yet — the
    # compliance stage never ran.
    assert paused_run.rules_evaluated == 0
    assert paused_run.findings == []


def test_reviewer_proceeds_and_run_completes(
    paused_run, run_service: BMRRunService
):
    resumed = run_service.resume_after_legibility(
        paused_run.run_id, action="proceed", actor_id="qa.reviewer"
    )
    assert resumed.status is RunStatus.COMPLETED
    assert resumed.rules_evaluated == 4
    # Provenance is preserved so the audit trail shows who proceeded.
    assert resumed.legibility_decision == "proceed"
    assert resumed.legibility_decided_by == "qa.reviewer"
    assert resumed.legibility_decided_at is not None
    assert resumed.legibility_reasons  # reasons survive the resume


def test_reviewer_reuploads_and_run_fails_with_note(
    paused_run, run_service: BMRRunService
):
    resumed = run_service.resume_after_legibility(
        paused_run.run_id,
        action="reupload",
        actor_id="qa.reviewer",
        note="scan is unreadable, requesting fresh pdf",
    )
    assert resumed.status is RunStatus.FAILED
    assert resumed.legibility_decision == "reupload"
    assert resumed.legibility_decision_note == "scan is unreadable, requesting fresh pdf"
    assert resumed.error is not None and "rejected" in resumed.error
    # Reupload does not run compliance.
    assert resumed.rules_evaluated == 0


def test_resume_rejects_unknown_action(
    paused_run, run_service: BMRRunService
):
    with pytest.raises(LegibilityDecisionError, match="unknown legibility action"):
        run_service.resume_after_legibility(
            paused_run.run_id, action="bogus", actor_id="qa.reviewer"
        )


def test_resume_rejects_when_run_is_not_paused(
    ingest_service, package_store, run_service: BMRRunService
):
    # A cleanly-classified package runs to COMPLETED without pausing,
    # so a resume call must be refused.
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
    assert report.status is RunStatus.COMPLETED

    with pytest.raises(LegibilityDecisionError, match="not awaiting"):
        run_service.resume_after_legibility(
            report.run_id, action="proceed", actor_id="qa.reviewer"
        )


def test_resume_rejects_unknown_run_id(run_service: BMRRunService):
    with pytest.raises(LegibilityDecisionError, match="not found"):
        run_service.resume_after_legibility(
            "nope", action="proceed", actor_id="qa.reviewer"
        )
