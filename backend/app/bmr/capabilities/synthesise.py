"""``checklist_synthesise.v1`` — roll up constituent findings into one draft.

Checklist synthesis rules reference a list of sibling rule ids via
``synthesises_from`` and group their findings by
``context_object.group_by`` (``bpcr_step`` | ``document_scope`` | ``rule``
| ``none``). Each group produces a single :class:`FindingDraft` whose
status is ``OPEN`` when any constituent is OPEN, ``INDETERMINATE`` when
any is indeterminate, otherwise ``PASS``.

The capability is pure: it receives the already-evaluated constituent
findings (i.e. the compliance stage calls it after all leaf rules have
been evaluated) and returns the roll-up drafts. No I/O.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.bmr.capabilities.evidence import (
    EvidenceRegion,
    FindingDraft,
    FindingSource,
    FindingStatus,
)

CAPABILITY_VERSION = "1"


def _rule_source_evidence(rule: dict[str, Any]) -> list[EvidenceRegion]:
    """Rule-source evidence for UNEVALUATED synthesis findings.

    Mirrors ``rule_eval._rule_source_evidence`` so that synthesis
    findings which never tie to a document still satisfy Constitution
    V's "every finding carries evidence" invariant.
    """

    return [
        EvidenceRegion(
            doc_id="__rule__",
            page_index=1,
            field="rule_source",
            note=f"rule:{rule.get('id', '<unknown>')}@{rule.get('version', '0.0.0')}",
        )
    ]


def _severity_rank(severity: str) -> int:
    ordering = {"critical": 4, "major": 3, "minor": 2, "info": 1, "observation": 0}
    return ordering.get(severity.lower(), 0)


def _group_key(finding: FindingDraft, *, group_by: str) -> tuple[str, dict[str, Any]]:
    if group_by == "bpcr_step" and finding.evidence:
        first = finding.evidence[0]
        return (f"step-{first.page_index:02d}", {"step_number": first.page_index})
    if group_by == "document_scope" and finding.evidence:
        first = finding.evidence[0]
        return (f"doc-{first.doc_id}", {"document_ref_id": first.doc_id})
    if group_by == "rule":
        return (finding.rule_id, {"constituent_rule_id": finding.rule_id})
    return ("all", {})


def _roll_up_status(findings: list[FindingDraft]) -> FindingStatus:
    statuses = {f.status for f in findings}
    if FindingStatus.OPEN in statuses:
        return FindingStatus.OPEN
    if FindingStatus.INDETERMINATE in statuses:
        return FindingStatus.INDETERMINATE
    if FindingStatus.UNEVALUATED in statuses and not (
        {FindingStatus.PASS} & statuses
    ):
        return FindingStatus.UNEVALUATED
    return FindingStatus.PASS


def _aggregate_evidence(findings: list[FindingDraft]) -> list[EvidenceRegion]:
    """Take one evidence region per constituent to keep the roll-up anchored."""

    seen: set[tuple[str, int, str | None]] = set()
    regions: list[EvidenceRegion] = []
    for finding in findings:
        for region in finding.evidence:
            key = (region.doc_id, region.page_index, region.field)
            if key in seen:
                continue
            seen.add(key)
            regions.append(region)
            break  # only the first region per constituent
    return regions


def checklist_synthesise_v1(
    *,
    rule: dict[str, Any],
    findings: Iterable[FindingDraft],
) -> list[FindingDraft]:
    ctx = rule.get("context_object", {}) or {}
    if ctx.get("scope") != "checklist_synthesis":
        raise ValueError(
            f"checklist_synthesise_v1 received rule with scope={ctx.get('scope')!r}"
        )

    referenced = [str(r) for r in (rule.get("synthesises_from") or [])]
    if not referenced:
        return [
            FindingDraft(
                rule_id=str(rule.get("id", "unknown")),
                rule_version=str(rule.get("version", "0.0.0")),
                status=FindingStatus.UNEVALUATED,
                severity=str(rule.get("severity", "observation")),
                alcoa_tag=rule.get("alcoa_tag"),
                gmp_category=rule.get("gmp_category"),
                summary="checklist_synthesis rule has no synthesises_from entries",
                detail="",
                source=FindingSource.CHECKLIST_SYNTHESIS,
                evidence=_rule_source_evidence(rule),
            )
        ]

    group_by = ctx.get("group_by", "none")
    referenced_set = set(referenced)

    constituents = [f for f in findings if f.rule_id in referenced_set]
    if not constituents:
        return [
            FindingDraft(
                rule_id=str(rule["id"]),
                rule_version=str(rule["version"]),
                status=FindingStatus.UNEVALUATED,
                severity=str(rule.get("severity", "observation")),
                alcoa_tag=rule.get("alcoa_tag"),
                gmp_category=rule.get("gmp_category"),
                summary=(
                    "checklist_synthesis could not find any constituent findings; "
                    "check that synthesises_from refers to rules that were evaluated"
                ),
                detail=f"referenced rule ids: {sorted(referenced)}",
                source=FindingSource.CHECKLIST_SYNTHESIS,
                evidence=_rule_source_evidence(rule),
            )
        ]

    groups: dict[str, tuple[dict[str, Any], list[FindingDraft]]] = {}
    for finding in constituents:
        key, ref = _group_key(finding, group_by=group_by)
        group = groups.setdefault(key, (ref, []))
        group[1].append(finding)

    results: list[FindingDraft] = []
    rule_id = str(rule["id"])
    rule_version = str(rule["version"])
    severity = str(rule.get("severity", "observation"))
    alcoa = rule.get("alcoa_tag")
    gmp = rule.get("gmp_category")

    for key, (group_ref, group_findings) in sorted(groups.items()):
        status = _roll_up_status(group_findings)
        # Always take the higher of (worst constituent severity, rule's
        # declared severity). Downgrading the rollup below a constituent's
        # severity would let a declared-info synthesis rule mask a
        # critical child finding from gating — regulatory miss with no
        # audit trail.
        worst = max(group_findings, key=lambda f: _severity_rank(f.severity)).severity
        effective_severity = (
            worst if _severity_rank(worst) >= _severity_rank(severity) else severity
        )

        open_count = sum(1 for f in group_findings if f.status is FindingStatus.OPEN)
        total = len(group_findings)
        checked_rules = sorted({f.rule_id for f in group_findings})
        scope_label = _scope_label(group_by, group_ref, key)
        summary = (
            f"Checklist roll-up ({scope_label}): "
            f"{open_count}/{total} constituent finding(s) open across "
            f"{len(checked_rules)} rule(s)"
        )
        detail = (
            f"Constituent rules: {', '.join(checked_rules)}\n"
            f"Group: {group_ref}\n"
            f"Statuses: "
            + ", ".join(f"{f.rule_id}={f.status.value}" for f in group_findings)
        )
        results.append(
            FindingDraft(
                rule_id=rule_id,
                rule_version=rule_version,
                status=status,
                severity=effective_severity,
                alcoa_tag=alcoa,
                gmp_category=gmp,
                summary=summary,
                detail=detail,
                source=FindingSource.CHECKLIST_SYNTHESIS,
                evidence=_aggregate_evidence(group_findings),
                fields={
                    "group_by": group_by,
                    "group_ref": group_ref,
                    "constituent_rule_ids": checked_rules,
                    "open_count": open_count,
                    "total_constituents": total,
                },
            )
        )
    return results


def _scope_label(group_by: str, group_ref: dict[str, Any], key: str) -> str:
    if group_by == "bpcr_step":
        return f"BPCR Step {group_ref.get('step_number', '?')}"
    if group_by == "document_scope":
        return f"document {group_ref.get('document_ref_id', '?')}"
    if group_by == "rule":
        return f"rule {group_ref.get('constituent_rule_id', key)}"
    return "all constituents"


__all__ = ["CAPABILITY_VERSION", "checklist_synthesise_v1"]
