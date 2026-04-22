"""Pydantic models for BMR audit runs and reports.

A :class:`RunReport` is the end-of-pipeline artefact that stage 5 emits
and the report stage / HITL (Spec 004) consume. ``FindingRecord`` is the
persisted form of a :class:`~app.bmr.capabilities.evidence.FindingDraft`
after the report stage assigns it a stable ``finding_id``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.bmr.capabilities.evidence import EvidenceRegion, FindingSource, FindingStatus


class RunStage(StrEnum):
    """Constitution II — 5-stage flat pipeline."""

    INGEST = "ingest"
    LEGIBILITY_AND_CLASSIFICATION = "legibility_and_classification"
    EXTRACTION = "extraction"
    COMPLIANCE = "compliance"
    REPORT = "report"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_LEGIBILITY_REVIEW = "awaiting_legibility_review"
    COMPLETED = "completed"
    FAILED = "failed"


class FindingRecord(BaseModel):
    """A finding that has been stamped with a run-scoped id."""

    finding_id: str
    rule_id: str
    rule_version: str
    # Spec 005 FR-005 — sha256 of the canonical rule body at load time.
    # Paired with ``rule_version`` (author semver) so reviewers can tell
    # whether two runs used literally the same rule bytes, even if the
    # author forgot to bump the semver.
    rule_content_hash: str = ""
    status: FindingStatus
    severity: str
    alcoa_tag: str | None = None
    gmp_category: str | None = None
    source: FindingSource
    summary: str
    detail: str = ""
    source_finding_ids: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRegion] = Field(default_factory=list)
    tolerance_applied: dict[str, Any] | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    # Spec 004 / follow-up #4 — selective re-run provenance.
    superseded_by: str | None = None
    supersedes: str | None = None

    model_config = ConfigDict(frozen=False)


class RunSummary(BaseModel):
    """Roll-up counts for a run's findings."""

    total: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_source: dict[str, int] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=False)


class RunReport(BaseModel):
    """The immutable(-ish) output of the REPORT stage."""

    run_id: str
    package_id: str
    status: RunStatus
    stage: RunStage
    rules_evaluated: int = 0
    # Spec 005 FR-013 — bank-level counters so operators can see at a
    # glance how many rules shipped in the bank vs. how many were
    # active vs. skipped because of deprecation.
    rules_loaded: int = 0
    rules_skipped_deprecated: int = 0
    findings: list[FindingRecord] = Field(default_factory=list)
    summary: RunSummary = Field(default_factory=RunSummary)
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    # Follow-up #4 — persisted so the HITL service can re-evaluate rules
    # when a reviewer submits a CORRECT action.
    rules_dir: str | None = None
    aliases_dir: str | None = None
    repo_root: str | None = None
    # Follow-up #2 — legibility HITL interrupt snapshot (populated only
    # when ``status == AWAITING_LEGIBILITY_REVIEW``).
    legibility_reasons: list[str] = Field(default_factory=list)
    legibility_decided_at: datetime | None = None
    legibility_decision: str | None = None  # "proceed" | "reupload"
    legibility_decided_by: str | None = None
    legibility_decision_note: str | None = None

    model_config = ConfigDict(frozen=False)


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


__all__ = [
    "FindingRecord",
    "RunReport",
    "RunStage",
    "RunStatus",
    "RunSummary",
    "now_utc",
]
