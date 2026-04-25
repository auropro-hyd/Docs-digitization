"""Tests for the three rule-evaluation capabilities."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.bmr.capabilities.aliases import load_alias_table
from app.bmr.capabilities.evidence import FindingStatus
from app.bmr.capabilities.extracted_data import (
    ExtractedPackage,
    ExtractedPage,
    FieldValue,
)
from app.bmr.capabilities.rule_eval import (
    cross_doc_rule_eval_v1,
    page_aggregate_eval_v1,
    same_page_eval_v1,
)

PILOT_RULES = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "rules"
    / "pilot"
    / "bank"
)
PILOT_ALIASES = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "rules"
    / "pilot"
    / "aliases"
    / "materials.yaml"
)


def _load_rule(name: str) -> dict:
    return yaml.safe_load((PILOT_RULES / name).read_text(encoding="utf-8"))


@pytest.fixture
def alias_tables() -> dict:
    table = load_alias_table(PILOT_ALIASES)
    # Rule YAML references 'backend/config/rules/pilot/aliases/materials.yaml'
    return {
        "backend/config/rules/pilot/aliases/materials.yaml": table,
    }


# ── same_page_eval.v1 ────────────────────────────────────────────────────────


def _bpcr_step_page(
    doc_id: str = "bpcr1",
    page_index: int = 1,
    *,
    signature: str | None = "john_doe",
) -> ExtractedPage:
    fields = []
    if signature is not None:
        fields.append(
            FieldValue(
                field="operator_signature",
                value=signature,
                source_doc_id=doc_id,
                source_page_index=page_index,
            )
        )
    return ExtractedPage(
        doc_id=doc_id,
        document_role="BPCR",
        page_index=page_index,
        tags=["bpcr_step_page"],
        fields=fields,
    )


def test_same_page_pass_when_signature_present():
    rule = _load_rule("alcoa_attributable_operator_signature.yaml")
    pkg = ExtractedPackage(
        package_id="pkg1",
        pages=[_bpcr_step_page(signature="jdoe")],
    )
    findings = same_page_eval_v1(rule=rule, extracted=pkg)
    assert findings == []


def test_same_page_emits_finding_when_signature_missing():
    rule = _load_rule("alcoa_attributable_operator_signature.yaml")
    pkg = ExtractedPackage(
        package_id="pkg1",
        pages=[_bpcr_step_page(signature=None)],
    )
    findings = same_page_eval_v1(rule=rule, extracted=pkg)
    assert len(findings) == 1
    f = findings[0]
    assert f.status == FindingStatus.OPEN
    assert f.severity == "critical"
    assert len(f.evidence) == 1
    assert f.evidence[0].doc_id == "bpcr1"
    assert f.evidence[0].page_index == 1


def test_same_page_no_matching_pages_yields_unevaluated():
    rule = _load_rule("alcoa_attributable_operator_signature.yaml")
    pkg = ExtractedPackage(package_id="pkg1", pages=[])
    findings = same_page_eval_v1(rule=rule, extracted=pkg)
    assert len(findings) == 1
    assert findings[0].status == FindingStatus.UNEVALUATED


# ── cross_doc_rule_eval.v1 ───────────────────────────────────────────────────


def _bpcr_step_with_weight(
    entity: str,
    weight: float,
    *,
    doc_id: str = "bpcr1",
    page_index: int = 2,
) -> ExtractedPage:
    return ExtractedPage(
        doc_id=doc_id,
        document_role="BPCR",
        page_index=page_index,
        tags=["bpcr_step_page"],
        fields=[
            FieldValue(
                field="dispensed_weight_kg",
                value=weight,
                entity_name=entity,
                source_doc_id=doc_id,
                source_page_index=page_index,
            )
        ],
    )


def _raw_material_page(
    entity: str,
    weight: float,
    *,
    doc_id: str = "rm1",
    page_index: int = 1,
) -> ExtractedPage:
    return ExtractedPage(
        doc_id=doc_id,
        document_role="RawMaterialPage",
        page_index=page_index,
        tags=["raw_material_page"],
        fields=[
            FieldValue(
                field="weight_kg",
                value=weight,
                entity_name=entity,
                source_doc_id=doc_id,
                source_page_index=page_index,
            )
        ],
    )


def test_cross_doc_pass_within_tolerance(alias_tables):
    # Under 'normalise' strategy the entity names only need to match
    # case-insensitively after punctuation stripping, not via alias lookup.
    rule = _load_rule("alcoa_accurate_bpcr_weight_match.yaml")
    pkg = ExtractedPackage(
        package_id="pkg1",
        pages=[
            _bpcr_step_with_weight("Lactose-Monohydrate", 10.05, page_index=2),
            _raw_material_page("lactose monohydrate", 10.0, page_index=1),
        ],
    )
    findings = cross_doc_rule_eval_v1(rule=rule, extracted=pkg, alias_tables=alias_tables)
    assert len(findings) == 1
    f = findings[0]
    assert f.status == FindingStatus.PASS, f.detail
    assert f.tolerance_applied == {"kind": "absolute", "value": 0.1, "unit": "kg"}
    assert len(f.evidence) == 2
    doc_ids = {e.doc_id for e in f.evidence}
    assert doc_ids == {"bpcr1", "rm1"}


def test_cross_doc_fail_beyond_tolerance(alias_tables):
    rule = _load_rule("alcoa_accurate_bpcr_weight_match.yaml")
    pkg = ExtractedPackage(
        package_id="pkg1",
        pages=[
            _bpcr_step_with_weight("Lactose-Monohydrate", 10.5, page_index=2),
            _raw_material_page("Lactose Monohydrate", 10.0, page_index=1),
        ],
    )
    findings = cross_doc_rule_eval_v1(rule=rule, extracted=pkg, alias_tables=alias_tables)
    assert len(findings) == 1
    assert findings[0].status == FindingStatus.OPEN


def test_cross_doc_missing_counterpart_fallback(alias_tables):
    rule = _load_rule("alcoa_accurate_bpcr_weight_match.yaml")
    pkg = ExtractedPackage(
        package_id="pkg1",
        pages=[_bpcr_step_with_weight("Some Unknown Material", 5.0, page_index=2)],
    )
    findings = cross_doc_rule_eval_v1(rule=rule, extracted=pkg, alias_tables=alias_tables)
    assert len(findings) == 1
    assert findings[0].status == FindingStatus.UNEVALUATED
    assert "counterpart" in findings[0].summary.lower() or "no counterpart" in findings[0].summary.lower()


def test_normalise_strategy_does_not_resolve_synonyms(alias_tables):
    # Sanity: 'normalise' is NOT the same as 'alias' — MCC and Avicel are
    # synonyms in the alias table but they normalise to distinct keys.
    rule = _load_rule("alcoa_accurate_bpcr_weight_match.yaml")
    pkg = ExtractedPackage(
        package_id="pkg1",
        pages=[
            _bpcr_step_with_weight("MCC", 20.0, page_index=2),
            _raw_material_page("Avicel", 20.05, page_index=1),
        ],
    )
    findings = cross_doc_rule_eval_v1(rule=rule, extracted=pkg, alias_tables=alias_tables)
    assert len(findings) == 1
    assert findings[0].status == FindingStatus.UNEVALUATED


def test_cross_doc_alias_strategy_resolves_mcc_to_avicel(alias_tables):
    # Explicitly build a rule dict that uses the 'alias' strategy.
    rule = _load_rule("alcoa_accurate_bpcr_weight_match.yaml")
    rule = dict(rule)  # shallow copy
    rule["context_object"] = {
        **rule["context_object"],
        "entity_match": {
            **rule["context_object"]["entity_match"],
            "strategy": "alias",
        },
    }
    pkg = ExtractedPackage(
        package_id="pkg1",
        pages=[
            _bpcr_step_with_weight("MCC", 20.0, page_index=2),
            _raw_material_page("Avicel", 20.05, page_index=1),
        ],
    )
    findings = cross_doc_rule_eval_v1(rule=rule, extracted=pkg, alias_tables=alias_tables)
    assert len(findings) == 1
    assert findings[0].status == FindingStatus.PASS, findings[0].detail


def test_cross_doc_no_source_pages_unevaluated(alias_tables):
    rule = _load_rule("alcoa_accurate_bpcr_weight_match.yaml")
    pkg = ExtractedPackage(package_id="pkg1", pages=[])
    findings = cross_doc_rule_eval_v1(rule=rule, extracted=pkg, alias_tables=alias_tables)
    assert len(findings) == 1
    assert findings[0].status == FindingStatus.UNEVALUATED


def test_cross_doc_multiple_counterparts_error(alias_tables):
    rule = _load_rule("alcoa_accurate_bpcr_weight_match.yaml")
    pkg = ExtractedPackage(
        package_id="pkg1",
        pages=[
            _bpcr_step_with_weight("Lactose Monohydrate", 10.0, page_index=2),
            _raw_material_page("Lactose Monohydrate", 10.0, page_index=1, doc_id="rm1"),
            _raw_material_page("Lactose Monohydrate", 10.1, page_index=1, doc_id="rm2"),
        ],
    )
    findings = cross_doc_rule_eval_v1(rule=rule, extracted=pkg, alias_tables=alias_tables)
    assert len(findings) == 1
    assert findings[0].status == FindingStatus.INDETERMINATE


# ── page_aggregate_eval.v1 ───────────────────────────────────────────────────


def test_page_aggregate_sum_pass():
    rule = {
        "schema_version": "1.0",
        "id": "alcoa.accurate.batch-sum",
        "version": "1.0.0",
        "severity": "major",
        "alcoa_tag": "Accurate",
        "description": "Sum ok",
        "context_object": {
            "scope": "page_aggregate",
            "page_selector": {
                "document_role": "BPCR",
                "page_filter": "all_bpcr_step_pages",
            },
            "aggregation": "sum",
        },
        "source": {"field": "dispensed_weight_kg"},
        "expected": {"field": "batch_target_weight_kg", "document_ref_hint": "BMR"},
        "tolerance": {"kind": "percent", "value": 0.5},
    }
    pkg = ExtractedPackage(
        package_id="pkg1",
        pages=[
            _bpcr_step_with_weight("A", 50.0, doc_id="bpcr", page_index=2),
            _bpcr_step_with_weight("B", 30.0, doc_id="bpcr", page_index=3),
            _bpcr_step_with_weight("C", 20.0, doc_id="bpcr", page_index=4),
            ExtractedPage(
                doc_id="bmr",
                document_role="BMR",
                page_index=1,
                tags=[],
                fields=[
                    FieldValue(
                        field="batch_target_weight_kg",
                        value=100.0,
                        source_doc_id="bmr",
                        source_page_index=1,
                    )
                ],
            ),
        ],
    )
    findings = page_aggregate_eval_v1(rule=rule, extracted=pkg)
    assert len(findings) == 1
    assert findings[0].status == FindingStatus.PASS, findings[0].detail
    assert findings[0].fields["aggregate_value"] == 100.0
    assert findings[0].fields["expected_value"] == 100.0
    assert findings[0].fields["sample_count"] == 3


def test_page_aggregate_fail_beyond_tolerance():
    rule = {
        "schema_version": "1.0",
        "id": "alcoa.accurate.batch-sum",
        "version": "1.0.0",
        "severity": "major",
        "alcoa_tag": "Accurate",
        "description": "Sum bad",
        "context_object": {
            "scope": "page_aggregate",
            "page_selector": {
                "document_role": "BPCR",
                "page_filter": "all_bpcr_step_pages",
            },
            "aggregation": "sum",
        },
        "source": {"field": "dispensed_weight_kg"},
        "expected": {"field": "batch_target_weight_kg", "document_ref_hint": "BMR"},
        "tolerance": {"kind": "absolute", "value": 0.1, "unit": "kg"},
    }
    pkg = ExtractedPackage(
        package_id="pkg1",
        pages=[
            _bpcr_step_with_weight("A", 50.0, page_index=2),
            _bpcr_step_with_weight("B", 50.0, page_index=3),
            ExtractedPage(
                doc_id="bmr",
                document_role="BMR",
                page_index=1,
                tags=[],
                fields=[
                    FieldValue(
                        field="batch_target_weight_kg",
                        value=101.0,
                        source_doc_id="bmr",
                        source_page_index=1,
                    )
                ],
            ),
        ],
    )
    findings = page_aggregate_eval_v1(rule=rule, extracted=pkg)
    assert findings[0].status == FindingStatus.OPEN
