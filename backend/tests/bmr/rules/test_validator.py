"""Tests for the deterministic rule validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.bmr.rules.loader import load_rule_bank, load_rule_file
from app.bmr.rules.validator import validate_rule_mapping

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "rules"
VALID = FIXTURES / "valid"
INVALID = FIXTURES / "invalid"


# ── Valid fixtures ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "cross_doc_weight_match.yaml",
        "page_aggregate_sum.yaml",
        "same_page_categorical.yaml",
    ],
)
def test_valid_fixture_loads(name: str):
    loaded, report = load_rule_file(VALID / name)
    assert report.ok, [e.to_dict() for e in report.errors]
    assert loaded is not None
    assert loaded.id.startswith("alcoa.")
    assert loaded.schema_version == "1.0"


def test_rule_bank_loads_all_valid_fixtures():
    bank = load_rule_bank(VALID)
    assert bank.ok
    ids = {r.id for r in bank.rules}
    assert {
        "alcoa.accurate.bpcr-raw-material-weight-match",
        "alcoa.accurate.bpcr-step-sum-vs-batch-target",
        "alcoa.attributable.operator-signature-present",
    }.issubset(ids)


# ── Invalid fixtures ─────────────────────────────────────────────────────────


def _err_paths_and_messages(path: Path) -> list[tuple[str, str]]:
    _, report = load_rule_file(path)
    assert not report.ok, f"expected failures for {path.name}"
    return [(e.path, e.message) for e in report.errors]


def test_missing_schema_version_is_blocking():
    paths = _err_paths_and_messages(INVALID / "missing_schema_version.yaml")
    assert any(p == "/schema_version" for p, _ in paths)


def test_cross_doc_missing_entity_match_has_fix_hint():
    _, report = load_rule_file(INVALID / "cross_doc_missing_entity_match.yaml")
    assert not report.ok
    assert any(
        "entity_match" in e.message.lower() and e.fix_hint and "normalise" in e.fix_hint
        for e in report.errors
    ), [e.to_dict() for e in report.errors]


def test_numeric_field_without_tolerance_blocks():
    _, report = load_rule_file(INVALID / "numeric_without_tolerance.yaml")
    assert not report.ok
    assert any(e.path == "/tolerance" and "Constitution" in e.message for e in report.errors)


def test_same_page_with_role_is_rejected():
    _, report = load_rule_file(INVALID / "same_page_with_role.yaml")
    assert not report.ok
    assert any("same_page" in e.message and "role" in e.message for e in report.errors)


def test_tolerance_zero_is_rejected():
    _, report = load_rule_file(INVALID / "tolerance_zero.yaml")
    assert not report.ok
    assert any(
        "tolerance.value" in e.message and "positive" in e.message for e in report.errors
    )


def test_unknown_property_is_rejected():
    _, report = load_rule_file(INVALID / "unknown_property.yaml")
    assert not report.ok
    assert any("unknown property" in e.message.lower() for e in report.errors)


def test_bad_id_and_version_patterns_report_distinct_errors():
    _, report = load_rule_file(INVALID / "bad_version.yaml")
    assert not report.ok
    paths = {e.path for e in report.errors}
    # id is uppercase so pattern fails; version is 1.0 so pattern fails.
    assert "/id" in paths
    assert "/version" in paths


# ── Unit-level validator ─────────────────────────────────────────────────────


def test_validate_mapping_rejects_non_dict():
    report = validate_rule_mapping("not a dict")  # type: ignore[arg-type]
    assert not report.ok
    assert report.errors[0].path == "/"


def test_validate_report_dict_shape_is_stable():
    _, report = load_rule_file(VALID / "cross_doc_weight_match.yaml")
    payload = report.to_dict()
    assert set(payload.keys()) == {
        "rule_id",
        "schema_version",
        "source_path",
        "ok",
        "errors",
        "warnings",
    }
