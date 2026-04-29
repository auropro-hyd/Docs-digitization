"""Finding + evidence dataclasses shared by all evaluation capabilities.

The rule evaluator never writes findings to storage directly; it returns
:class:`FindingDraft` records that the pipeline's report stage persists.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FindingStatus(StrEnum):
    OPEN = "open"
    PASS = "pass"
    INDETERMINATE = "indeterminate"
    UNEVALUATED = "unevaluated"


class FindingSource(StrEnum):
    """How a finding was produced (see Spec 001 data-model.md)."""

    ALCOA = "alcoa"
    GMP = "gmp"
    CHECKLIST = "checklist"
    # Synthesised finding rolls up multiple upstream findings — tracked here
    # for completeness even though v0 capabilities don't emit it.
    CHECKLIST_SYNTHESIS = "checklist_synthesis"


class EvidenceRegion(BaseModel):
    """Pointer to a page region used by a finding."""

    doc_id: str
    page_index: int = Field(ge=1)
    field: str | None = None
    value: Any | None = None
    bbox: tuple[float, float, float, float] | None = None
    note: str | None = None
    # Spec 007 — copied through from the matched ExtractedPage when the
    # page belongs to a sectioned document (BPCR with detection on).
    # Stays ``None`` for non-BPCR pages and for runs with section
    # detection disabled — keeps existing audit-trail JSON noise-free.
    section_id: str | None = None

    model_config = ConfigDict(frozen=True)


class FindingDraft(BaseModel):
    """A draft finding emitted by a capability (not yet persisted)."""

    rule_id: str
    rule_version: str
    # Spec 005 FR-005: content hash of the rule at load time. Empty
    # string is allowed for legacy call-sites (notably fixtures the
    # fixture-run CLI constructs directly) but the compliance stage
    # populates it for every finding emitted by a loaded rule.
    rule_content_hash: str = ""
    status: FindingStatus
    severity: str
    alcoa_tag: str | None = None
    gmp_category: str | None = None
    summary: str
    detail: str = ""
    source: FindingSource
    source_finding_ids: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRegion] = Field(default_factory=list)
    tolerance_applied: dict[str, Any] | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    # Marks findings that were produced by a rule's ``fallback`` policy
    # (e.g. ``treat_as_pass`` when an entity could not be resolved).
    # Reviewers and severity gating need to treat these distinctly from
    # genuine rule-matched outcomes. None = the rule evaluated normally.
    fallback_applied: str | None = None

    model_config = ConfigDict(frozen=False)


__all__ = [
    "EvidenceRegion",
    "FindingDraft",
    "FindingSource",
    "FindingStatus",
]
