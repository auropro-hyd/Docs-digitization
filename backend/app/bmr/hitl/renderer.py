"""HTML + PDF rendering for audit report exports.

Design:

- :class:`ReportRenderer` is a tiny protocol so tests can inject a stub
  renderer without pulling in WeasyPrint's native dependencies (Pango /
  libgobject). Production defaults to :class:`WeasyPrintRenderer`, which
  falls back to a "PDF-ish" pass-through if WeasyPrint can't initialise.
- HTML template is inlined (no Jinja2 dep yet) — small enough that
  f-string interpolation is both safer and clearer.
"""

from __future__ import annotations

import html
import logging
from typing import Protocol

from app.bmr.hitl.models import (
    ExportGateStatus,
    FeedbackSample,
    GroupedReport,
    ReportSection,
    StructuredResolution,
)
from app.bmr.workflow.models import FindingRecord, RunReport

logger = logging.getLogger(__name__)


class ReportRenderer(Protocol):
    def render_html(
        self,
        *,
        run_report: RunReport,
        grouped_report: GroupedReport,
        resolutions: list[StructuredResolution],
    ) -> str: ...

    def render_pdf(self, html_body: str) -> bytes: ...


# ── HTML template ────────────────────────────────────────────────────────────


_HTML_SHELL = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>BMR Audit Report — {run_id}</title>
  <style>
    body {{ font-family: -apple-system, system-ui, sans-serif; font-size: 11pt; color: #222; }}
    h1 {{ font-size: 18pt; margin: 0 0 4pt 0; }}
    h2 {{ font-size: 14pt; margin: 18pt 0 4pt 0; color: #111; }}
    h3 {{ font-size: 11pt; margin: 10pt 0 2pt 0; color: #333; }}
    .meta {{ color: #555; font-size: 9pt; margin-bottom: 18pt; }}
    .section {{ border: 1px solid #ccc; border-radius: 4pt; padding: 8pt; margin: 6pt 0; }}
    .severity-counts {{ color: #555; font-size: 9pt; }}
    .badge {{ display: inline-block; padding: 1pt 6pt; border-radius: 8pt; font-size: 9pt; margin-left: 4pt; }}
    .badge.critical {{ background: #a00; color: #fff; }}
    .badge.major {{ background: #c60; color: #fff; }}
    .badge.minor {{ background: #888; color: #fff; }}
    .badge.info   {{ background: #eee; color: #222; }}
    .finding {{ border-top: 1px dashed #ddd; padding: 6pt 0; }}
    .finding:first-child {{ border-top: none; }}
    .status-pass   {{ color: #060; }}
    .status-open   {{ color: #a00; }}
    .status-indeterminate {{ color: #c60; }}
    .resolution {{ background: #f4f8f4; padding: 4pt 6pt; border-left: 3pt solid #060; margin-top: 4pt; }}
    .evidence {{ color: #555; font-size: 9pt; }}
    .gate {{ padding: 6pt 10pt; border-radius: 4pt; margin: 10pt 0; font-weight: bold; }}
    .gate.ready {{ background: #eafbea; color: #060; }}
    .gate.blocked {{ background: #fdeaea; color: #a00; }}
  </style>
</head>
<body>
  <h1>BMR Audit Report</h1>
  <div class="meta">
    <div><strong>Run:</strong> {run_id}</div>
    <div><strong>Package:</strong> {package_id}</div>
    <div><strong>Rules evaluated:</strong> {rules_evaluated}</div>
    <div><strong>Total findings:</strong> {total_findings}</div>
    <div><strong>Exported at:</strong> {exported_at}</div>
  </div>
  <div class="gate {gate_class}">Export gate: {gate_status} (pending blocking: {pending})</div>
  {sections_html}
</body>
</html>
"""


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _render_finding(
    finding: FindingRecord,
    resolution: StructuredResolution | None,
) -> str:
    status_class = f"status-{finding.status.value}"
    evidence_html = "".join(
        f"<div class=\"evidence\">• doc {_esc(e.doc_id)}, page {_esc(e.page_index)}"
        + (f", field {_esc(e.field)}" if e.field else "")
        + (f" — <em>{_esc(e.note)}</em>" if e.note else "")
        + "</div>"
        for e in finding.evidence
    )
    resolution_html = ""
    if resolution is not None:
        reason = (
            f" — {_esc(resolution.reason_type.value)}"
            if resolution.reason_type is not None
            else ""
        )
        observed = (
            f"<div>Observed on document: <em>{_esc(resolution.observed_value_on_document)}</em></div>"
            if resolution.observed_value_on_document
            else ""
        )
        comment = (
            f"<div>Comment: {_esc(resolution.reason_comment)}</div>"
            if resolution.reason_comment
            else ""
        )
        resolution_html = (
            '<div class="resolution">'
            f"<strong>Resolved:</strong> {_esc(resolution.action.value)}{reason}"
            f"<div>By {_esc(resolution.actor_id)} at {_esc(resolution.created_at.isoformat())}</div>"
            f"{observed}{comment}</div>"
        )
    return (
        '<div class="finding">'
        f'<div><span class="{status_class}"><strong>{_esc(finding.status.value.upper())}</strong></span> '
        f'<span class="badge {finding.severity.lower()}">{_esc(finding.severity)}</span> '
        f"{_esc(finding.summary)}</div>"
        f"<div class=\"evidence\">Rule: {_esc(finding.rule_id)} v{_esc(finding.rule_version)}</div>"
        f"{evidence_html}{resolution_html}"
        "</div>"
    )


def _render_section(
    section: ReportSection,
    findings_by_id: dict[str, FindingRecord],
    active_resolutions: dict[str, StructuredResolution],
) -> str:
    title = f"Section {section.id} ({section.group_kind.value})"
    if section.group_kind.value == "bpcr_step":
        step = section.group_ref.get("step_number")
        title = f"BPCR Step {step}"
    elif section.group_kind.value == "document_scope":
        title = f"Document scope: {section.group_ref.get('document_ref_id', 'unknown')}"
    counts = section.severity_counts
    counts_html = (
        f"critical={counts.critical}, major={counts.major}, "
        f"minor={counts.minor}, info={counts.info}"
    )
    sub_html: list[str] = []
    for sub in section.sub_sections:
        if not sub.finding_ids:
            continue
        rows = "".join(
            _render_finding(findings_by_id[fid], active_resolutions.get(fid))
            for fid in sub.finding_ids
            if fid in findings_by_id
        )
        sub_html.append(
            f"<h3>{_esc(sub.kind.value.upper())}</h3>{rows}"
        )
    all_actioned = "✓ all actioned" if section.all_actioned else "⚠︎ pending"
    return (
        '<div class="section">'
        f"<h2>{_esc(title)} <small>— {all_actioned}</small></h2>"
        f'<div class="severity-counts">{counts_html}</div>'
        + "".join(sub_html)
        + "</div>"
    )


class WeasyPrintRenderer:
    """Default renderer. Falls back to HTML bytes if WeasyPrint can't render."""

    def render_html(
        self,
        *,
        run_report: RunReport,
        grouped_report: GroupedReport,
        resolutions: list[StructuredResolution],
    ) -> str:
        findings_by_id = {f.finding_id: f for f in run_report.findings}
        active_resolutions = {
            r.finding_id: r for r in resolutions if not r.needs_re_action
        }
        sections_html = "".join(
            _render_section(s, findings_by_id, active_resolutions)
            for s in grouped_report.sections
        )
        gate_class = (
            "ready"
            if grouped_report.export_gate is ExportGateStatus.READY
            else "blocked"
        )
        exported_at = (
            run_report.finished_at.isoformat()
            if run_report.finished_at
            else run_report.started_at.isoformat()
        )
        return _HTML_SHELL.format(
            run_id=_esc(run_report.run_id),
            package_id=_esc(run_report.package_id),
            rules_evaluated=_esc(run_report.rules_evaluated),
            total_findings=_esc(len(run_report.findings)),
            exported_at=_esc(exported_at),
            gate_class=gate_class,
            gate_status=_esc(grouped_report.export_gate.value),
            pending=_esc(grouped_report.pending_blocking_count),
            sections_html=sections_html or "<p><em>No findings.</em></p>",
        )

    def render_pdf(self, html_body: str) -> bytes:
        try:
            import weasyprint  # noqa: PLC0415 — optional heavy import
        except ImportError:
            logger.warning("WeasyPrint not installed; returning raw HTML bytes")
            return html_body.encode("utf-8")
        try:
            return weasyprint.HTML(string=html_body).write_pdf() or b""
        except OSError as exc:
            logger.warning(
                "WeasyPrint missing native deps (%s); returning raw HTML bytes",
                exc,
            )
            return html_body.encode("utf-8")


__all__ = ["ReportRenderer", "WeasyPrintRenderer"]


# ── bundle JSON serialisation ────────────────────────────────────────────────


def render_bundle_json(
    *,
    run_report: RunReport,
    grouped_report: GroupedReport,
    resolutions: list[StructuredResolution],
    feedback_samples: list[FeedbackSample],
) -> bytes:
    import json  # noqa: PLC0415 — stdlib, keep local

    payload = {
        "run": run_report.model_dump(mode="json"),
        "grouped_report": grouped_report.model_dump(mode="json"),
        "resolutions": [r.model_dump(mode="json") for r in resolutions],
        "feedback_samples": [f.model_dump(mode="json") for f in feedback_samples],
    }
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
