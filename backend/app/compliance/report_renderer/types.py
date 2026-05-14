"""Render-time types for Spec 008 — never persisted.

The on-disk JSON shape (``ComplianceReport``) remains the source of
truth. These dataclasses are derived at render time by the
``builder.build_report_document()`` pure function and consumed by
the HTML / PDF / Markdown renderers + the on-screen rule-table
component (via the ``/report-rows`` API endpoint).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

ComplianceKind = Literal["compliant", "action_required", "needs_attention"]


@dataclass(frozen=True)
class ReportRow:
    """One row of the client-aligned rule-centric report."""

    rule_id: str
    agent: str
    question: str
    compliance_label: str
    compliance_kind: ComplianceKind
    evidence_pages: str
    detailed_evidence: str
    mitigation: str


@dataclass(frozen=True)
class ReportHeader:
    """Top-of-document metadata block."""

    product_name: str
    title: str
    is_draft: bool
    metadata_rows: list[tuple[str, str]]
    logo_path: Path | None


@dataclass(frozen=True)
class ReportFooter:
    """Per-page disclaimer footer."""

    operator_name: str
    generated_at: datetime
    disclaimer: str
    """Pre-rendered disclaimer string for the renderer to drop into
    each page's footer band — the builder fills the template with
    the operator name + timestamp once."""


@dataclass(frozen=True)
class ReportStats:
    """Counts derived from the rows; used by ``/report-rows`` JSON
    response and by post-export telemetry."""

    row_count: int
    compliant_count: int
    action_required_count: int
    needs_attention_count: int
    excluded_not_applicable_count: int


@dataclass(frozen=True)
class ReportDocument:
    """Complete render-time payload for any renderer or the
    ``/report-rows`` endpoint."""

    header: ReportHeader
    rows: list[ReportRow]
    footer: ReportFooter
    stats: ReportStats
    priority_actions: list[str] = field(default_factory=list)
    key_risks: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)


def report_document_to_dict(doc: "ReportDocument") -> dict:
    """JSON-friendly view for ``/report-rows``.

    Path / datetime values are normalised to strings here so the
    route handler doesn't need a custom encoder. The shape mirrors
    the frontend ``ReportDocument`` TypeScript type.
    """
    return {
        "header": {
            "product_name": doc.header.product_name,
            "title": doc.header.title,
            "is_draft": doc.header.is_draft,
            "metadata_rows": [[label, value] for label, value in doc.header.metadata_rows],
            "logo_path": str(doc.header.logo_path) if doc.header.logo_path else None,
        },
        "rows": [
            {
                "rule_id": r.rule_id,
                "agent": r.agent,
                "question": r.question,
                "compliance_label": r.compliance_label,
                "compliance_kind": r.compliance_kind,
                "evidence_pages": r.evidence_pages,
                "detailed_evidence": r.detailed_evidence,
                "mitigation": r.mitigation,
            }
            for r in doc.rows
        ],
        "footer": {
            "operator_name": doc.footer.operator_name,
            "generated_at": doc.footer.generated_at.isoformat(),
            "disclaimer": doc.footer.disclaimer,
        },
        "stats": {
            "row_count": doc.stats.row_count,
            "compliant_count": doc.stats.compliant_count,
            "action_required_count": doc.stats.action_required_count,
            "needs_attention_count": doc.stats.needs_attention_count,
            "excluded_not_applicable_count": doc.stats.excluded_not_applicable_count,
        },
    }
