"""Models for the BMR finding-level HITL surface.

Narrow v0 subset of Spec 004's data-model: CONFIRM/DISMISS resolutions,
FeedbackSample records, grouped report projection, export-gate status,
and audit report revisions. CORRECT + CorrectionWorkflow land in a later
slice.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.bmr.workflow.models import FindingRecord

# ── enums ────────────────────────────────────────────────────────────────────


class ResolutionAction(StrEnum):
    CONFIRM = "CONFIRM"
    DISMISS = "DISMISS"
    CORRECT = "CORRECT"  # accepted at the contract boundary but v0 returns 501


class DismissReasonType(StrEnum):
    """YAML-extensible seed set (data-model §1.2)."""

    OCR_MISREAD = "OCR_MISREAD"
    ACCEPTABLE_VARIANCE = "ACCEPTABLE_VARIANCE"
    DUPLICATE_FINDING = "DUPLICATE_FINDING"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    RULE_MISCONFIGURED = "RULE_MISCONFIGURED"
    OTHER = "OTHER"


# These reason types require an ``observed_value_on_document`` because they
# imply a disagreement with the extracted value (Spec 004 §3.1).
_VALUE_DEPENDENT_DISMISS_REASONS: frozenset[DismissReasonType] = frozenset(
    {DismissReasonType.OCR_MISREAD, DismissReasonType.ACCEPTABLE_VARIANCE}
)


def reason_requires_observed_value(reason: DismissReasonType) -> bool:
    return reason in _VALUE_DEPENDENT_DISMISS_REASONS


class ExportGateStatus(StrEnum):
    READY = "ready"
    BLOCKED_BY_PENDING_FINDINGS = "blocked_by_pending_findings"
    BLOCKED_BY_STALE_RESOLUTIONS = "blocked_by_stale_resolutions"


class GroupKind(StrEnum):
    BPCR_STEP = "bpcr_step"
    DOCUMENT_SCOPE = "document_scope"


class SubSectionKind(StrEnum):
    ALCOA = "alcoa"
    GMP = "gmp"
    CHECKLIST = "checklist"


# ── persisted entities ───────────────────────────────────────────────────────


class StructuredResolution(BaseModel):
    """Structured reviewer action on a finding (CONFIRM/DISMISS for v0).

    Frozen so the audit trail cannot be mutated after save; subsequent
    reviewer action produces a *new* resolution that ``supersedes_id``
    the previous one (see Spec 004 §3.2).
    """

    resolution_id: str
    run_id: str
    finding_id: str
    action: ResolutionAction
    reason_type: DismissReasonType | None = None
    observed_value_on_document: str | None = None
    system_extracted_value: Any | None = None
    reason_comment: str | None = None
    duplicate_of_finding_id: str | None = None
    supersedes_id: str | None = None
    needs_re_action: bool = False
    actor_id: str
    created_at: datetime

    model_config = ConfigDict(frozen=True)


class FeedbackSample(BaseModel):
    """One row of the training/tuning corpus consumed by Spec 005."""

    sample_id: str
    run_id: str
    finding_id: str
    resolution_id: str
    rule_id: str
    rule_version: str
    action: ResolutionAction
    reason_type: DismissReasonType | None = None
    finding_snapshot: FindingRecord
    input_context_digest: str
    created_at: datetime

    model_config = ConfigDict(frozen=False)


class CorrectionStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    FAILED = "failed"


class CorrectionWorkflow(BaseModel):
    """Reviewer-authored field correction + selective re-run summary."""

    workflow_id: str
    run_id: str
    finding_id: str
    rule_id: str
    field: str
    doc_id: str
    page_index: int = Field(ge=1)
    previous_value: Any | None = None
    corrected_value: Any
    reason_comment: str
    resolution_id: str
    actor_id: str
    status: CorrectionStatus = CorrectionStatus.PENDING
    affected_rule_ids: list[str] = Field(default_factory=list)
    superseded_finding_ids: list[str] = Field(default_factory=list)
    new_finding_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    applied_at: datetime | None = None
    error: str | None = None

    model_config = ConfigDict(frozen=False)


class AuditReportRevision(BaseModel):
    """Immutable export record (one per successful export)."""

    revision_id: str
    run_id: str
    revision_number: int = Field(ge=1)
    predecessor_id: str | None = None
    pdf_sha256: str
    bundle_sha256: str
    pdf_bytes_stored_path: str
    bundle_stored_path: str
    exported_by: str
    exported_at: datetime
    findings_snapshot: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(frozen=True)


# ── projection view-models (not persisted) ───────────────────────────────────


class SeverityCounts(BaseModel):
    critical: int = 0
    major: int = 0
    minor: int = 0
    info: int = 0

    model_config = ConfigDict(frozen=False)


class ResolutionSubSection(BaseModel):
    kind: SubSectionKind
    finding_ids: list[str] = Field(default_factory=list)

    model_config = ConfigDict(frozen=True)


class ReportSection(BaseModel):
    id: str
    group_kind: GroupKind
    group_ref: dict[str, Any] = Field(default_factory=dict)
    title: str = ""
    sub_sections: list[ResolutionSubSection] = Field(default_factory=list)
    severity_counts: SeverityCounts = Field(default_factory=SeverityCounts)
    all_actioned: bool = False

    model_config = ConfigDict(frozen=False)


class GroupedReport(BaseModel):
    run_id: str
    view: str = "grouped"
    sections: list[ReportSection] = Field(default_factory=list)
    flat_finding_ids: list[str] = Field(default_factory=list)
    export_gate: ExportGateStatus
    pending_blocking_count: int = 0

    model_config = ConfigDict(frozen=False)


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


__all__ = [
    "AuditReportRevision",
    "CorrectionStatus",
    "CorrectionWorkflow",
    "DismissReasonType",
    "ExportGateStatus",
    "FeedbackSample",
    "GroupKind",
    "GroupedReport",
    "ReportSection",
    "ResolutionAction",
    "ResolutionSubSection",
    "SeverityCounts",
    "StructuredResolution",
    "SubSectionKind",
    "now_utc",
    "reason_requires_observed_value",
]
