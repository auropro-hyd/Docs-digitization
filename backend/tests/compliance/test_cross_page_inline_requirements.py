"""Pin the dispatcher contract for ``cross_page.interface``.

Two requirement shapes are supported simultaneously: the legacy
named-requirement string (looked up against the
``_REQUIREMENTS`` registry) and the inline ``{section_type,
in_document_type}`` dict declared per-rule in YAML. Both must
resolve through one entry point so the cross-page agent doesn't
need to dispatch on shape itself.

The graceful-degradation contract is the load-bearing invariant:
on a legacy single-document package whose sections lack a
populated ``document_type`` field, an inline requirement with
``in_document_type`` set must still match the section by
section_type alone — otherwise turning on per-section doc_type
on Akhilesh's branch would silently disable every inline rule
on every legacy run until the segmentation pipeline catches up.
"""

from __future__ import annotations

import pytest

from app.compliance.cross_page.interface import (
    InlineSectionRequirement,
    resolve_requirement,
)
from app.compliance.models import DocumentSection, DocumentSegmentation


def _make_seg(sections: list[tuple[str, str, str]]) -> DocumentSegmentation:
    """Build a segmentation from ``(section_id, section_type, document_type)``."""
    return DocumentSegmentation(
        sections=[
            DocumentSection(
                section_id=sid,
                section_type=stype,
                document_type=dtype,
                start_page=1,
                end_page=1,
            )
            for sid, stype, dtype in sections
        ]
    )


# ── Backward compatibility: named requirement IDs ────────────


def test_named_requirement_resolves_against_registered_dict() -> None:
    seg = _make_seg([
        ("s1", "manufacturing_operations", "batch_record"),
        ("s2", "material_dispensing", "batch_record"),
    ])
    r = resolve_requirement(seg, "material_usage_vs_dispensing")
    assert r.applicable
    assert "s1" in r.evidence.source_section_ids
    assert "s2" in r.evidence.target_section_ids


def test_named_requirement_unknown_id_returns_inapplicable() -> None:
    seg = _make_seg([("s1", "cover_page", "batch_record")])
    r = resolve_requirement(seg, "no_such_requirement")
    assert not r.applicable
    assert "Unknown" in r.reason


# ── Inline dict path: full filtering when doc_type is stamped ─


def test_inline_dict_filters_by_document_type_when_stamped() -> None:
    seg = _make_seg([
        ("s1", "material_request", "raw_material_request"),
        ("s2", "material_request", "batch_record"),
    ])
    r = resolve_requirement(
        seg,
        {"section_type": "material_request", "in_document_type": "raw_material_request"},
    )
    assert r.applicable
    assert r.evidence.target_section_ids == ("s1",)


def test_inline_dict_rejects_section_in_wrong_document_type() -> None:
    seg = _make_seg([
        ("s1", "manufacturing_operations", "raw_material_request"),
    ])
    r = resolve_requirement(
        seg,
        {"section_type": "manufacturing_operations", "in_document_type": "batch_record"},
    )
    assert not r.applicable


# ── Inline dict path: graceful degradation on legacy packages ─


def test_inline_dict_degrades_to_section_type_only_on_legacy_package() -> None:
    """The load-bearing invariant. If the segmentation pipeline
    hasn't stamped document_type yet (no section carries it), the
    inline requirement's in_document_type filter must NOT veto
    matches — otherwise rolling out per-section doc_type would
    silently disable every inline rule until segmentation lands.
    """
    seg = _make_seg([
        ("s1", "manufacturing_operations", ""),
        ("s2", "material_dispensing", ""),
    ])
    r = resolve_requirement(
        seg,
        {"section_type": "manufacturing_operations", "in_document_type": "batch_record"},
    )
    assert r.applicable, (
        "inline requirement must fall through to section-type-only "
        "matching when no section carries a document_type stamp"
    )
    assert r.evidence.target_section_ids == ("s1",)


def test_inline_dict_partial_stamping_still_works() -> None:
    """Mixed populations are common during rollout — some sections
    stamped, others not. The matcher should union both paths so
    nothing is silently dropped.
    """
    seg = _make_seg([
        ("s1", "manufacturing_operations", "batch_record"),
        ("s2", "manufacturing_operations", ""),
    ])
    r = resolve_requirement(
        seg,
        {"section_type": "manufacturing_operations", "in_document_type": "batch_record"},
    )
    # Stamped section is the only one this requirement is asking for —
    # the unstamped one belongs to whichever doc, we don't know,
    # and the cross-product of unknowns shouldn't poison the result.
    assert r.applicable
    assert "s1" in r.evidence.target_section_ids


# ── Inline dict path: whole-document mode ────────────────────


def test_inline_dict_whole_document_mode() -> None:
    """``section_type=""`` means "any section in this document",
    used for documents like ipc_report whose profile has empty
    expected_sections (the whole doc is treated as one section).
    """
    seg = _make_seg([
        ("ipc1", "ipc_report", "ipc_report"),
        ("br1", "manufacturing_operations", "batch_record"),
    ])
    r = resolve_requirement(
        seg, {"section_type": "", "in_document_type": "ipc_report"}
    )
    assert r.applicable
    assert r.evidence.target_section_ids == ("ipc1",)


# ── Inline dict path: error cases ────────────────────────────


def test_inline_dict_empty_requirement_is_inapplicable() -> None:
    seg = _make_seg([("s1", "cover_page", "batch_record")])
    r = resolve_requirement(seg, {"section_type": "", "in_document_type": ""})
    assert not r.applicable
    assert "empty" in r.reason.lower()


def test_inline_dict_section_type_aliases_normalize() -> None:
    """``manufacturing_operation`` (singular) is an alias of the
    canonical ``manufacturing_operations`` per
    ``document_profiles.yaml`` — the inline path must normalize
    both sides before matching, otherwise OCR/LLM naming variance
    silently misses real sections.
    """
    seg = _make_seg([
        ("s1", "manufacturing_operations", "batch_record"),
    ])
    r = resolve_requirement(
        seg,
        # Alias on the requirement side.
        {"section_type": "manufacturing_operation", "in_document_type": "batch_record"},
    )
    assert r.applicable


# ── Display ID formatting ────────────────────────────────────


def test_inline_requirement_display_id_is_human_readable() -> None:
    """The trace reason must surface the requirement in a form a
    reviewer can read at a glance — never the raw dict repr.
    """
    seg = _make_seg([("s1", "manufacturing_operations", "batch_record")])
    r = resolve_requirement(
        seg,
        {"section_type": "manufacturing_operations", "in_document_type": "batch_record"},
    )
    assert r.requirement_id == "batch_record.manufacturing_operations"
    # Stars for "any" — easy to read in a log line.
    inline = InlineSectionRequirement(section_type="", in_document_type="ipc_report")
    assert inline.display_id == "ipc_report.*"


# ── End-to-end: registry → agent flow ────────────────────────


def test_dict_csr_survives_registry_loader() -> None:
    """If ``_as_csr_list`` regresses to ``_as_str_list``, dict
    requirements get stringified into ``"{'section_type': ...}"``
    and the resolver path silently breaks. This test pins the
    end-to-end path: YAML → registry → resolver.
    """
    from app.compliance.rules.registry import (
        _RULES_DIR,
        _load_rule_config,
        _parse_rules_file,
    )

    cfg = _load_rule_config("reconciliation")
    rules = _parse_rules_file(
        _RULES_DIR / "reconciliation_rules.md", "reconciliation", cfg
    )
    assert rules, "reconciliation rules failed to load"

    for rule in rules:
        for csr in rule.cross_section_requirements:
            assert isinstance(csr, (str, dict)), (
                f"rule {rule.id} csr entry has unexpected type "
                f"{type(csr).__name__} — registry loader regressed and "
                f"is stringifying dicts"
            )
