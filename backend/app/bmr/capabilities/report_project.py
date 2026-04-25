"""``report_project.v1`` — group findings into the grouped-report view-model.

Deliberately pure: takes a :class:`RunReport` plus the active resolutions
for the run and returns a :class:`GroupedReport`. No I/O, no randomness.

v0 grouping heuristic (Spec 004 §2.3):

- If a finding's first evidence region points at a BPCR page, infer a
  ``bpcr_step`` section keyed by ``step_number`` = ``page_index``.
- Otherwise bucket the finding under a ``document_scope`` section keyed
  by the first evidence ``doc_id`` (or ``unknown`` if empty).

Sub-sections (ALCOA / GMP / Checklist) follow the finding's ``source``
bucket. Severity gating uses the blocking set passed in; default is
``{"critical", "major"}``.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.bmr.capabilities.evidence import FindingStatus
from app.bmr.hitl.models import (
    ExportGateStatus,
    GroupedReport,
    GroupKind,
    ReportSection,
    ResolutionSubSection,
    SeverityCounts,
    StructuredResolution,
    SubSectionKind,
)
from app.bmr.hitl.reporting_config import (
    ReportSectionsConfig,
    SeverityGatingConfig,
)
from app.bmr.workflow.models import FindingRecord, FindingSource, RunReport

CAPABILITY_VERSION = "1"

DEFAULT_BLOCKING_SEVERITIES: frozenset[str] = frozenset({"critical", "major"})

_SOURCE_TO_SUB_KIND: dict[str, SubSectionKind] = {
    FindingSource.ALCOA.value: SubSectionKind.ALCOA,
    FindingSource.GMP.value: SubSectionKind.GMP,
    FindingSource.CHECKLIST.value: SubSectionKind.CHECKLIST,
    FindingSource.CHECKLIST_SYNTHESIS.value: SubSectionKind.CHECKLIST,
}


def _infer_section(finding: FindingRecord) -> tuple[str, GroupKind, dict]:
    if not finding.evidence:
        return "doc-unknown", GroupKind.DOCUMENT_SCOPE, {"document_ref_id": "unknown"}
    first = finding.evidence[0]
    if _is_bpcr_step_page(finding):
        step = first.page_index
        return f"step-{step:02d}", GroupKind.BPCR_STEP, {"step_number": step}
    return (
        f"doc-{first.doc_id}",
        GroupKind.DOCUMENT_SCOPE,
        {"document_ref_id": first.doc_id},
    )


def _is_bpcr_step_page(finding: FindingRecord) -> bool:
    # v0 heuristic: a cross-doc weight-match finding has two evidence regions
    # and the first one lives on a BPCR step page. Same-page operator-signature
    # findings also belong in the BPCR step bucket.
    first = finding.evidence[0] if finding.evidence else None
    if first is None:
        return False
    note = (first.note or "").lower()
    if "bpcr_step" in note:
        return True
    return len(finding.evidence) >= 2


def _bump_severity(counts: SeverityCounts, severity: str) -> None:
    key = severity.lower()
    if key == "critical":
        counts.critical += 1
    elif key == "major":
        counts.major += 1
    elif key == "minor":
        counts.minor += 1
    else:
        counts.info += 1


def _finding_is_blocking(
    finding: FindingRecord,
    severity_config: SeverityGatingConfig,
) -> bool:
    if finding.status != FindingStatus.OPEN:
        return False
    if finding.superseded_by is not None:
        return False
    return severity_config.is_blocking(
        rule_id=finding.rule_id, severity=finding.severity
    )


def report_project_v1(
    *,
    run_report: RunReport,
    resolutions: Iterable[StructuredResolution] = (),
    blocking_severities: Iterable[str] | None = None,
    severity_config: SeverityGatingConfig | None = None,
    sections_config: ReportSectionsConfig | None = None,
    view: str = "grouped",
) -> GroupedReport:
    if severity_config is None:
        if blocking_severities is not None:
            severity_config = SeverityGatingConfig(
                blocking_severities=frozenset(
                    s.lower() for s in blocking_severities
                )
            )
        else:
            severity_config = SeverityGatingConfig()
    sections_cfg = sections_config or ReportSectionsConfig()

    active_by_finding = {r.finding_id: r for r in resolutions if not r.needs_re_action}

    sections_by_id: dict[str, ReportSection] = {}
    flat_ids: list[str] = []
    pending_blocking = 0

    for finding in run_report.findings:
        flat_ids.append(finding.finding_id)
        section_id, group_kind, group_ref = _infer_section(finding)
        section = sections_by_id.get(section_id)
        if section is None:
            section = ReportSection(
                id=section_id,
                group_kind=group_kind,
                group_ref=group_ref,
                sub_sections=[
                    ResolutionSubSection(kind=k, finding_ids=[])
                    for k in sections_cfg.sub_section_order
                ],
                severity_counts=SeverityCounts(),
                all_actioned=True,
            )
            sections_by_id[section_id] = section

        sub_kind = _SOURCE_TO_SUB_KIND.get(
            finding.source.value, SubSectionKind.CHECKLIST
        )
        target_sub = next(
            (sub for sub in section.sub_sections if sub.kind is sub_kind), None
        )
        if target_sub is None:
            target_sub = ResolutionSubSection(kind=sub_kind, finding_ids=[])
            section.sub_sections.append(target_sub)
        target_sub.finding_ids.append(finding.finding_id)

        _bump_severity(section.severity_counts, finding.severity)

        is_blocking = _finding_is_blocking(finding, severity_config)
        has_active_resolution = finding.finding_id in active_by_finding
        if is_blocking and not has_active_resolution:
            section.all_actioned = False
            pending_blocking += 1

    for section in sections_by_id.values():
        if not section.title:
            section.title = sections_cfg.render_title(
                group_kind=section.group_kind, group_ref=section.group_ref
            )

    sections = sorted(
        sections_by_id.values(),
        key=lambda s: (sections_cfg.group_rank(s.group_kind), s.id),
    )

    if pending_blocking > 0:
        gate_status = ExportGateStatus.BLOCKED_BY_PENDING_FINDINGS
    elif any(r.needs_re_action for r in resolutions):
        gate_status = ExportGateStatus.BLOCKED_BY_STALE_RESOLUTIONS
    else:
        gate_status = ExportGateStatus.READY

    return GroupedReport(
        run_id=run_report.run_id,
        view=view,
        sections=sections,
        flat_finding_ids=flat_ids,
        export_gate=gate_status,
        pending_blocking_count=pending_blocking,
    )


__all__ = ["CAPABILITY_VERSION", "DEFAULT_BLOCKING_SEVERITIES", "report_project_v1"]
