"""Schema v1.1 back-compat + section_id rejection tests (Spec 007 FR-014).

The 1.0 → 1.1 bump adds an optional ``page_selector.section_id``. The
contract:

- v1.0 rules MUST validate against v1.0 unchanged.
- v1.1 rules MUST be able to declare ``section_id``.
- ``unsectioned`` is reserved for the detector and MUST be rejected
  by the v1.1 schema (``not: { const: "unsectioned" }``).
- v1.1 schema must continue to validate every existing v1.0-shaped
  rule when the ``schema_version`` is bumped — proves the addition
  is purely additive.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.bmr.rules.schema import available_schema_versions, load_schema
from app.bmr.rules.validator import validate_rule_mapping

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "rules"


def test_schema_v1_1_is_published() -> None:
    versions = available_schema_versions()
    assert "1.0" in versions
    assert "1.1" in versions, "Spec 007 added v1.1; loader must discover it"


def test_v1_0_schema_still_loads_unchanged() -> None:
    schema = load_schema("1.0")
    assert schema["properties"]["schema_version"]["const"] == "1.0"
    # The 1.0 page_selector MUST NOT have section_id — the field
    # belongs to the 1.1 surface.
    page_sel_props = schema["$defs"]["ContextObject"]["properties"][
        "page_selector"
    ]["properties"]
    assert "section_id" not in page_sel_props


def test_v1_1_schema_introduces_section_id() -> None:
    schema = load_schema("1.1")
    assert schema["properties"]["schema_version"]["const"] == "1.1"
    page_sel_props = schema["$defs"]["ContextObject"]["properties"][
        "page_selector"
    ]["properties"]
    assert "section_id" in page_sel_props, (
        "Spec 007 FR-014 requires page_selector.section_id under v1.1"
    )
    assert page_sel_props["section_id"]["pattern"] == "^[a-z][a-z0-9_]*$"
    # The reserved sentinel cannot be authored.
    assert page_sel_props["section_id"]["not"] == {"const": "unsectioned"}


def _v1_0_rule(**overrides: object) -> dict:
    rule = {
        "schema_version": "1.0",
        "id": "alcoa.attributable.test-rule",
        "version": "1.0.0",
        "severity": "minor",
        "alcoa_tag": "Attributable",
        "description": "Smoke test rule for schema parity.",
        "context_object": {"scope": "same_page"},
        "source": {"field": "operator_signature", "scope_hint": "bpcr_step_page"},
    }
    rule.update(overrides)
    return rule


def test_v1_0_rules_unaffected_by_v1_1_existence() -> None:
    rule = _v1_0_rule()
    report = validate_rule_mapping(rule, source_path=Path("synthetic.yaml"))
    assert report.ok, report.to_dict()


def test_v1_1_rule_with_section_id_validates() -> None:
    rule = _v1_0_rule(
        schema_version="1.1",
        id="alcoa.accurate.section-aware",
        alcoa_tag="Accurate",
        context_object={
            "scope": "page_aggregate",
            "page_selector": {
                "document_role": "BPCR",
                "page_filter": "all_bpcr_step_pages",
                "section_id": "yield_calculation",
            },
            "aggregation": "sum",
        },
        source={"field": "dispensed_weight_kg"},
        expected={"field": "batch_target_weight_kg", "document_ref_hint": "BMR"},
        tolerance={"kind": "percent", "value": 0.5},
        fallback="flag_as_unevaluated",
    )
    report = validate_rule_mapping(rule, source_path=Path("synthetic.yaml"))
    assert report.ok, report.to_dict()


def test_v1_1_schema_rejects_reserved_unsectioned_section_id() -> None:
    rule = _v1_0_rule(
        schema_version="1.1",
        id="alcoa.accurate.bad-section-id",
        alcoa_tag="Accurate",
        context_object={
            "scope": "page_aggregate",
            "page_selector": {
                "document_role": "BPCR",
                "page_filter": "all_bpcr_step_pages",
                "section_id": "unsectioned",
            },
            "aggregation": "sum",
        },
        source={"field": "dispensed_weight_kg"},
        expected={"field": "batch_target_weight_kg", "document_ref_hint": "BMR"},
        tolerance={"kind": "percent", "value": 0.5},
        fallback="flag_as_unevaluated",
    )
    report = validate_rule_mapping(rule, source_path=Path("synthetic.yaml"))
    assert not report.ok
    # The validator surfaces the offending path so authors can fix it.
    bodies = " ".join(err.message + " " + err.path for err in report.errors)
    assert "section_id" in bodies


def test_v1_1_schema_rejects_invalid_section_id_pattern() -> None:
    rule = _v1_0_rule(
        schema_version="1.1",
        id="alcoa.accurate.bad-section-id-pattern",
        alcoa_tag="Accurate",
        context_object={
            "scope": "page_aggregate",
            "page_selector": {
                "document_role": "BPCR",
                "page_filter": "all_bpcr_step_pages",
                "section_id": "Yield Calculation",  # spaces + caps
            },
            "aggregation": "sum",
        },
        source={"field": "dispensed_weight_kg"},
        expected={"field": "batch_target_weight_kg", "document_ref_hint": "BMR"},
        tolerance={"kind": "percent", "value": 0.5},
        fallback="flag_as_unevaluated",
    )
    report = validate_rule_mapping(rule, source_path=Path("synthetic.yaml"))
    assert not report.ok


def test_existing_v1_0_fixtures_still_validate() -> None:
    """Walk every fixture under ``valid/`` and re-validate as a back-compat sanity check."""

    valid_dir = FIXTURES / "valid"
    if not valid_dir.is_dir():
        return
    for fixture in valid_dir.glob("*.yaml"):
        mapping = yaml.safe_load(fixture.read_text(encoding="utf-8"))
        report = validate_rule_mapping(mapping, source_path=fixture)
        assert report.ok, (
            f"{fixture.name} stopped validating after Spec 007: {report.to_dict()}"
        )
