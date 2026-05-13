"""Markdown renderer — fallback format for ops users / CI."""

from __future__ import annotations

from app.compliance.report_renderer.types import ReportDocument


def render_md(doc: ReportDocument) -> str:
    """Render ``doc`` to a Markdown table form."""

    lines: list[str] = []
    lines.append(f"# {doc.header.title}")
    lines.append("")
    lines.append(f"**{doc.header.product_name}**")
    if doc.header.is_draft:
        lines.append("")
        lines.append("> _Document is Draft_")
    lines.append("")

    # Metadata table.
    lines.append("| | |")
    lines.append("|---|---|")
    for label, value in doc.header.metadata_rows:
        lines.append(f"| **{label}** | {_escape(value)} |")
    lines.append("")

    # Rule table — no scores per Spec 008 FR-007.
    if doc.rows:
        lines.append(
            "| Question | Compliance | Evidence From Document | "
            "Detailed Evidence | Mitigation |"
        )
        lines.append("|---|---|---|---|---|")
        for row in doc.rows:
            badge = {
                "compliant": "✓ Compliant",
                "action_required": "⚠ Action Required",
                "needs_attention": "! Needs Attention",
            }[row.compliance_kind]
            lines.append(
                f"| {_escape(row.question)} | {badge} | "
                f"{_escape(row.evidence_pages)} | "
                f"{_escape(row.detailed_evidence)} | "
                f"{_escape(row.mitigation)} |"
            )
    else:
        lines.append("_No applicable rules evaluated for this document._")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"_{doc.footer.disclaimer}_")

    return "\n".join(lines)


def _escape(value: str) -> str:
    """Escape Markdown-table-breaking characters inline."""
    if not value:
        return ""
    # Pipes break the table cell; newlines also break the row.
    return (
        value.replace("|", "\\|")
             .replace("\n", " ")
             .replace("\r", " ")
    )
