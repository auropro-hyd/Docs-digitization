"""Reusable cross-section resolver primitives.

Two shapes of cross-section requirement are supported, in this order
of historical precedence:

1. **Named (string-id) requirement** — a stable ID that maps to a
   ``CrossSectionRequirement`` registered in ``_REQUIREMENTS``. These
   carry a *comparator* (``material_usage_vs_dispensed``,
   ``result_identifier_consistency``, …) so the cross-page evaluator
   knows what semantic check to run on the matched pair. Used by
   evaluator-driven flows that need a comparator hook.

2. **Inline dict requirement** — an ad-hoc
   ``{section_type, in_document_type}`` filter declared on a single
   rule's YAML. Used by gating rules whose ``pass_criteria`` text
   already describes the verdict; the resolver only needs to confirm
   that the required section(s) are present in the package. Joins
   on document_type when ``DocumentSection.document_type`` is
   populated, and degrades gracefully to section-type-only matching
   on legacy single-document packages where it isn't.

Both shapes resolve to the same ``CrossSectionResolution`` so the
caller (``cross_page.agent``) doesn't need to dispatch on shape
itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from app.compliance.models import DocumentSegmentation
from app.compliance.rules.profiles import (
    normalize_document_type,
    normalize_section_type,
)


@dataclass(frozen=True)
class CrossSectionRequirement:
    """A named requirement registered in ``_REQUIREMENTS``."""

    requirement_id: str
    source_section_types: tuple[str, ...]
    target_section_types: tuple[str, ...]
    comparator: str
    description: str


@dataclass(frozen=True)
class InlineSectionRequirement:
    """An ad-hoc ``{section_type, in_document_type}`` filter parsed
    from a rule's YAML ``cross_section_requirements`` list.

    Either field may be empty:
      * ``section_type=""`` → match any section in the document
      * ``in_document_type=""`` → match any document with this section
      * both empty → invalid; the resolver surfaces it as inapplicable

    The string form ``f"{document_type}.{section_type}"`` is what
    appears in the resolution's ``requirement_id``, ``reason``, and
    on-the-wire trace.
    """

    section_type: str
    in_document_type: str

    @classmethod
    def from_dict(cls, raw: dict[str, str]) -> "InlineSectionRequirement":
        return cls(
            section_type=normalize_section_type(str(raw.get("section_type") or "")),
            in_document_type=normalize_document_type(
                str(raw.get("in_document_type") or "")
            ),
        )

    @property
    def display_id(self) -> str:
        doc = self.in_document_type or "*"
        sec = self.section_type or "*"
        return f"{doc}.{sec}"


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


# ── Registered named requirements ────────────────────────────

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


# ── Indexing helpers (shared by both resolution paths) ───────


def _index_sections(seg: DocumentSegmentation) -> dict[
    tuple[str, str], list[str]
]:
    """Index sections by ``(document_type, section_type)``.

    ``document_type`` may be empty when the legacy single-document
    pipeline hasn't stamped it; we still index those sections under
    ``("", section_type)`` so the section-type-only path can find
    them. Section types are normalized so OCR/LLM naming variance
    converges to canonical IDs from ``document_profiles.yaml``.
    """

    index: dict[tuple[str, str], list[str]] = {}
    for sec in seg.sections:
        doc_type = normalize_document_type(sec.document_type) if sec.document_type else ""
        sec_type = normalize_section_type(sec.section_type)
        index.setdefault((doc_type, sec_type), []).append(sec.section_id)
    return index


def _section_ids_matching(
    index: dict[tuple[str, str], list[str]],
    section_types: tuple[str, ...],
    in_document_type: str = "",
) -> list[str]:
    """Look up section IDs under any of ``section_types``, optionally
    filtered to a single ``in_document_type``.

    When ``in_document_type`` is empty we union across all document
    types — this is both the legacy named-requirement contract and
    the graceful-degradation path for inline requirements on a
    package where ``DocumentSection.document_type`` hasn't been
    populated yet.
    """

    out: list[str] = []
    for stype in section_types:
        norm = normalize_section_type(stype)
        if in_document_type:
            out.extend(index.get((in_document_type, norm), []))
            # If no section carries the doc_type stamp, fall through
            # to the unstamped bucket so legacy packages still match.
            if not any(k[0] == in_document_type for k in index):
                out.extend(index.get(("", norm), []))
        else:
            for (_doc, sec_t), ids in index.items():
                if sec_t == norm:
                    out.extend(ids)
    return out


# ── Public dispatcher ────────────────────────────────────────


# Accepts either a registered requirement-ID string or an inline
# ``{section_type, in_document_type}`` dict from rule YAML.
RawRequirement = Union[str, dict[str, str], InlineSectionRequirement]


def resolve_requirement(
    segmentation: DocumentSegmentation,
    requirement: RawRequirement,
) -> CrossSectionResolution:
    """Resolve either a named or inline cross-section requirement
    against a segmentation. The two paths share the same indexing
    and matching helpers, so adding a new requirement shape is a
    matter of routing — not duplicating lookup logic.
    """

    if isinstance(requirement, dict):
        return _resolve_inline(
            segmentation, InlineSectionRequirement.from_dict(requirement)
        )
    if isinstance(requirement, InlineSectionRequirement):
        return _resolve_inline(segmentation, requirement)
    return _resolve_named(segmentation, str(requirement))


def _resolve_named(
    segmentation: DocumentSegmentation, requirement_id: str
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

    index = _index_sections(segmentation)
    source_ids = _section_ids_matching(index, req.source_section_types)
    target_ids = _section_ids_matching(index, req.target_section_types)

    pairs: list[tuple[str, str]] = [
        (sid, tid)
        for sid in source_ids
        for tid in target_ids
        if sid != tid
    ]

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


def _resolve_inline(
    segmentation: DocumentSegmentation,
    requirement: InlineSectionRequirement,
) -> CrossSectionResolution:
    if not requirement.section_type and not requirement.in_document_type:
        empty = CrossSectionEvidence((), (), (), comparator="presence_gate")
        return CrossSectionResolution(
            requirement_id=requirement.display_id,
            applicable=False,
            reason="Inline requirement is empty — needs section_type and/or in_document_type",
            evidence=empty,
        )

    index = _index_sections(segmentation)

    if requirement.section_type:
        section_types: tuple[str, ...] = (requirement.section_type,)
    else:
        # ``section_type=""`` means "any section in this document".
        section_types = tuple(
            sec_t
            for (doc_t, sec_t) in index
            if not requirement.in_document_type or doc_t == requirement.in_document_type
        ) or ("",)

    matched_ids = _section_ids_matching(
        index, section_types, in_document_type=requirement.in_document_type
    )

    applicable = bool(matched_ids)

    if applicable:
        scope = (
            f"in document_type '{requirement.in_document_type}'"
            if requirement.in_document_type
            else "across all documents"
        )
        sec_label = requirement.section_type or "*any*"
        reason = (
            f"Matched {len(matched_ids)} section(s) of type '{sec_label}' "
            f"{scope}"
        )
    else:
        reason = (
            f"No section matched inline requirement '{requirement.display_id}' "
            f"in this package"
        )

    # Inline requirements are presence gates, not source/target
    # comparisons — so we put the matched ids in target_section_ids
    # to align with the cross_page agent's matched_section_ids
    # gathering (which unions source + target). source stays empty
    # to make the semantic distinction explicit in traces.
    evidence = CrossSectionEvidence(
        source_section_ids=(),
        target_section_ids=tuple(matched_ids),
        matched_pairs=(),
        comparator="presence_gate",
    )
    return CrossSectionResolution(
        requirement_id=requirement.display_id,
        applicable=applicable,
        reason=reason,
        evidence=evidence,
    )
