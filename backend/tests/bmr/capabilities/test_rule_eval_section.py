"""Selector tests for ``page_selector.section_id`` (Spec 007 FR-014–FR-018).

When a rule sets ``section_id`` under ``page_selector``, the engine
must filter to pages whose ``ExtractedPage.section_id`` matches.
Pages that never received a section assignment (the section enricher
was off or failed) MUST NOT match — the rule should degrade via its
existing ``fallback`` policy (FR-016). Rules without ``section_id``
MUST behave exactly as in v1.0 (back-compat invariant).

We exercise this directly through ``page_aggregate_eval_v1`` since
that is the first selector site that consumes ``section_id`` in v0.
"""

from __future__ import annotations

from app.bmr.capabilities.evidence import FindingStatus
from app.bmr.capabilities.extracted_data import (
    ExtractedPackage,
    ExtractedPage,
    FieldValue,
)
from app.bmr.capabilities.rule_eval import page_aggregate_eval_v1


def _bpcr_pages(weights: list[float], section_ids: list[str | None]) -> list[ExtractedPage]:
    assert len(weights) == len(section_ids)
    return [
        ExtractedPage(
            doc_id="bpcr",
            document_role="BPCR",
            page_index=i + 1,
            section_id=sid,
            fields=[
                FieldValue(
                    field="dispensed_weight_kg",
                    value=str(w),
                    source_doc_id="bpcr",
                    source_page_index=i + 1,
                )
            ],
        )
        for i, (w, sid) in enumerate(zip(weights, section_ids, strict=True))
    ]


def _bmr_target_page(target: float) -> ExtractedPage:
    return ExtractedPage(
        doc_id="bmr",
        document_role="BMR",
        page_index=1,
        fields=[
            FieldValue(
                field="batch_target_weight_kg",
                value=str(target),
                source_doc_id="bmr",
                source_page_index=1,
            )
        ],
    )


def _yield_section_rule() -> dict:
    return {
        "id": "alcoa.accurate.bpcr-yield-section-vs-batch-target",
        "version": "1.0.0",
        "severity": "major",
        "alcoa_tag": "Accurate",
        "context_object": {
            "scope": "page_aggregate",
            "page_selector": {
                "document_role": "BPCR",
                "page_filter": "all_bpcr_step_pages",
                "section_id": "yield_calculation",
            },
            "aggregation": "sum",
        },
        "fallback": "flag_as_unevaluated",
        "source": {"field": "dispensed_weight_kg"},
        "expected": {"field": "batch_target_weight_kg", "document_ref_hint": "BMR"},
        "tolerance": {"kind": "percent", "value": 0.5},
    }


def _legacy_v1_0_rule() -> dict:
    """Same shape but no section_id — proves back-compat."""

    rule = _yield_section_rule()
    del rule["context_object"]["page_selector"]["section_id"]
    return rule


# ── FR-014 / FR-015: section_id filter applies ─────────────────────────────


def test_section_aware_rule_aggregates_only_matching_pages() -> None:
    pages = _bpcr_pages(
        weights=[1.0, 2.0, 3.0, 4.0],
        section_ids=[
            "material_dispensing",
            "material_dispensing",
            "yield_calculation",
            "yield_calculation",
        ],
    )
    pages.append(_bmr_target_page(target=7.0))
    extracted = ExtractedPackage(package_id="pkg", pages=pages)
    # Pages tagged 'bpcr_step_page' so the existing page_filter matches.
    extracted = ExtractedPackage(
        package_id="pkg",
        pages=[p.model_copy(update={"tags": ["bpcr_step_page"]}) for p in pages[:-1]]
        + [pages[-1]],
    )

    findings = page_aggregate_eval_v1(
        rule=_yield_section_rule(), extracted=extracted, alias_tables={}
    )

    assert len(findings) == 1
    finding = findings[0]
    # Only pages 3+4 (weights 3+4=7) are summed; matches target 7.0.
    assert finding.status is FindingStatus.PASS
    assert finding.fields["aggregate_value"] == 7.0
    assert finding.fields["sample_count"] == 2


def test_section_aware_rule_carries_section_id_on_evidence() -> None:
    pages = _bpcr_pages(
        weights=[3.0, 4.0],
        section_ids=["yield_calculation", "yield_calculation"],
    )
    pages.append(_bmr_target_page(target=7.0))
    extracted = ExtractedPackage(
        package_id="pkg",
        pages=[p.model_copy(update={"tags": ["bpcr_step_page"]}) for p in pages[:-1]]
        + [pages[-1]],
    )

    findings = page_aggregate_eval_v1(
        rule=_yield_section_rule(), extracted=extracted, alias_tables={}
    )

    section_evidence = [
        ev for ev in findings[0].evidence if ev.note == "source_aggregated"
    ]
    assert section_evidence, "expected source_aggregated evidence on the finding"
    assert all(ev.section_id == "yield_calculation" for ev in section_evidence)


# ── FR-016: degradation when section data is absent ────────────────────────


def test_section_aware_rule_unevaluated_when_no_pages_have_section_id() -> None:
    # All BPCR pages exist but section_id is None (detection disabled).
    pages = _bpcr_pages(weights=[1.0, 2.0], section_ids=[None, None])
    pages.append(_bmr_target_page(target=3.0))
    extracted = ExtractedPackage(
        package_id="pkg",
        pages=[p.model_copy(update={"tags": ["bpcr_step_page"]}) for p in pages[:-1]]
        + [pages[-1]],
    )

    findings = page_aggregate_eval_v1(
        rule=_yield_section_rule(), extracted=extracted, alias_tables={}
    )

    assert len(findings) == 1
    assert findings[0].status is FindingStatus.UNEVALUATED


# ── Back-compat: v1.0 rules unaffected ─────────────────────────────────────


def test_v1_0_rule_without_section_id_uses_all_matching_pages() -> None:
    pages = _bpcr_pages(
        weights=[1.0, 2.0, 3.0, 4.0],
        section_ids=[
            "material_dispensing",
            "material_dispensing",
            "yield_calculation",
            "yield_calculation",
        ],
    )
    pages.append(_bmr_target_page(target=10.0))
    extracted = ExtractedPackage(
        package_id="pkg",
        pages=[p.model_copy(update={"tags": ["bpcr_step_page"]}) for p in pages[:-1]]
        + [pages[-1]],
    )

    findings = page_aggregate_eval_v1(
        rule=_legacy_v1_0_rule(), extracted=extracted, alias_tables={}
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.fields["aggregate_value"] == 10.0
    assert finding.fields["sample_count"] == 4
    assert finding.status is FindingStatus.PASS
    # source_aggregated evidence still carries section_id whenever the
    # underlying page has one — the rule didn't ask, but we don't drop
    # information already present on the page (additive semantics).
    # Expected-value evidence has no section_id (it points at a BMR
    # page which was never sectioned).
    aggregated_evidence = [
        ev for ev in finding.evidence if ev.note == "source_aggregated"
    ]
    assert {ev.section_id for ev in aggregated_evidence} == {
        "material_dispensing",
        "yield_calculation",
    }
