"""Reusable cross-section resolver primitives and deterministic matching."""

from __future__ import annotations

from dataclasses import dataclass

from app.compliance.models import DocumentSegmentation
from app.compliance.rules.profiles import normalize_section_type


@dataclass(frozen=True)
class CrossSectionRequirement:
    requirement_id: str
    source_section_types: tuple[str, ...]
    target_section_types: tuple[str, ...]
    comparator: str
    description: str


@dataclass(frozen=True)
class CrossSectionEvidence:
    source_section_ids: tuple[str, ...]
    target_section_ids: tuple[str, ...]
    matched_pairs: tuple[tuple[str, str], ...]
    comparator: str


@dataclass(frozen=True)
class CrossSectionResolution:
    requirement_id: str
    applicable: bool
    reason: str
    evidence: CrossSectionEvidence


_REQUIREMENTS: dict[str, CrossSectionRequirement] = {
    "operation_vs_weighing_reconciliation": CrossSectionRequirement(
        requirement_id="operation_vs_weighing_reconciliation",
        source_section_types=("manufacturing_operations",),
        target_section_types=("material_dispensing",),
        comparator="material_usage_vs_dispensed",
        description="Verify manufacturing usage against raw material weighing/dispensing.",
    ),
    "material_usage_vs_dispensing": CrossSectionRequirement(
        requirement_id="material_usage_vs_dispensing",
        source_section_types=("manufacturing_operations",),
        target_section_types=("material_dispensing",),
        comparator="material_usage_vs_dispensed",
        description="Check material usage entries reconcile with dispensed quantities.",
    ),
    "sample_sent_vs_qc_report": CrossSectionRequirement(
        requirement_id="sample_sent_vs_qc_report",
        source_section_types=("manufacturing_operations", "sampling"),
        target_section_types=("qc_report", "certificate_of_analysis"),
        comparator="sample_reference_presence",
        description="Check that samples sent to QCD have corresponding reports.",
    ),
    "qc_vs_coa_consistency": CrossSectionRequirement(
        requirement_id="qc_vs_coa_consistency",
        source_section_types=("qc_report",),
        target_section_types=("certificate_of_analysis",),
        comparator="result_identifier_consistency",
        description="Compare QC report outputs with CoA data.",
    ),
    "inter_section_consistency": CrossSectionRequirement(
        requirement_id="inter_section_consistency",
        source_section_types=("manufacturing_operations", "material_dispensing"),
        target_section_types=("qc_report", "in_process_report", "yield_calculation"),
        comparator="cross_section_consistency",
        description="General consistency checks across manufacturing, QC and yield sections.",
    ),
}


def get_requirement(requirement_id: str) -> CrossSectionRequirement | None:
    return _REQUIREMENTS.get(requirement_id)


def resolve_requirement(
    segmentation: DocumentSegmentation,
    requirement_id: str,
) -> CrossSectionResolution:
    req = get_requirement(requirement_id)
    if req is None:
        empty = CrossSectionEvidence((), (), (), comparator="unknown")
        return CrossSectionResolution(
            requirement_id=requirement_id,
            applicable=False,
            reason=f"Unknown cross-section requirement '{requirement_id}'",
            evidence=empty,
        )

    section_ids_by_type: dict[str, list[str]] = {}
    for sec in segmentation.sections:
        stype = normalize_section_type(sec.section_type)
        section_ids_by_type.setdefault(stype, []).append(sec.section_id)

    source_ids: list[str] = []
    for st in req.source_section_types:
        source_ids.extend(section_ids_by_type.get(normalize_section_type(st), []))

    target_ids: list[str] = []
    for st in req.target_section_types:
        target_ids.extend(section_ids_by_type.get(normalize_section_type(st), []))

    pairs: list[tuple[str, str]] = []
    for sid in source_ids:
        for tid in target_ids:
            if sid != tid:
                pairs.append((sid, tid))

    applicable = bool(source_ids and target_ids)
    if applicable:
        reason = (
            f"Resolved {len(source_ids)} source and {len(target_ids)} target "
            f"sections for comparator '{req.comparator}'"
        )
    else:
        reason = "Required source/target sections not present in this document"

    evidence = CrossSectionEvidence(
        source_section_ids=tuple(source_ids),
        target_section_ids=tuple(target_ids),
        matched_pairs=tuple(pairs),
        comparator=req.comparator,
    )
    return CrossSectionResolution(
        requirement_id=requirement_id,
        applicable=applicable,
        reason=reason,
        evidence=evidence,
    )
