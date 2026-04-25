"""LangGraph state for the BMR audit pipeline.

We use :class:`TypedDict` (not Pydantic) because LangGraph's channel
reducers treat ``TypedDict`` as the canonical state type. All nested
payloads are kept as already-validated Pydantic objects; LangGraph will
reduce them via last-write-wins at each node.
"""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict

from app.bmr.capabilities.evidence import FindingDraft
from app.bmr.capabilities.extracted_data import ExtractedPackage
from app.bmr.ingest.models import DocumentPackage
from app.bmr.workflow.models import RunReport, RunStage, RunStatus


class BMRRunState(TypedDict, total=False):
    """Mutable run state flowing through the LangGraph."""

    run_id: str
    package_id: str
    rules_dir: str
    aliases_dir: str
    extraction_path: str
    repo_root: str
    started_at: datetime

    stage: RunStage
    status: RunStatus
    error: str

    package: DocumentPackage
    extracted: ExtractedPackage

    rules_evaluated: int
    # Spec 005 FR-013 — bank-level counters. ``rules_loaded`` is the
    # total rules in the bank (including deprecated ones that got
    # parsed but skipped). ``rules_skipped_deprecated`` is how many of
    # those carried ``deprecated: true``.
    rules_loaded: int
    rules_skipped_deprecated: int
    findings: list[FindingDraft]
    report: RunReport

    # Follow-up #2 — legibility HITL interrupt.
    legibility_override: bool
    legibility_reasons: list[str]


__all__ = ["BMRRunState"]
