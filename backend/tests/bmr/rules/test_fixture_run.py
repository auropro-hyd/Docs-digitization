"""Tests for :mod:`app.bmr.rules.fixture_run` (Spec 005 authoring loop).

These tests drive the same entry point the ``bmr-rules fixture-run`` CLI
and the ``bmr-rule-author`` skill use. The point of the module is to
prove "this rule fires on this fixture" without standing up the full
BMR pipeline — so these tests exercise only :func:`run_rule_against_fixture`
plus its public report shape.
"""

from __future__ import annotations

from pathlib import Path

from app.bmr.rules.fixture_run import run_rule_against_fixture

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "rules"
REPO_ROOT = Path(__file__).resolve().parents[4]  # <repo>/ (parent of backend/)
VALID_RULES = FIXTURE_ROOT / "valid"
INVALID_RULES = FIXTURE_ROOT / "invalid"
EXTRACTION_FIXTURES = FIXTURE_ROOT / "fixtures"


def test_cross_doc_rule_fires_on_mismatch_fixture():
    report = run_rule_against_fixture(
        rule_path=VALID_RULES / "cross_doc_weight_match.yaml",
        fixture_path=EXTRACTION_FIXTURES / "bpcr_weight_mismatch.json",
        repo_root=REPO_ROOT,
        expected="fires",
    )
    assert report.ok, report.to_dict()
    assert report.scope == "cross_document"
    assert report.rule_id == "alcoa.accurate.bpcr-raw-material-weight-match"
    assert report.fired
    assert report.expectation_met is True
    statuses = [f.status.value for f in report.findings]
    assert "open" in statuses
    open_finding = next(f for f in report.findings if f.status.value == "open")
    # Evidence must point at both documents (Constitution V).
    doc_ids = {e.doc_id for e in open_finding.evidence}
    assert {"bpcr", "raw"}.issubset(doc_ids)


def test_cross_doc_rule_does_not_fire_on_matching_fixture():
    report = run_rule_against_fixture(
        rule_path=VALID_RULES / "cross_doc_weight_match.yaml",
        fixture_path=EXTRACTION_FIXTURES / "bpcr_weight_match.json",
        repo_root=REPO_ROOT,
        expected="not_fires",
    )
    assert report.ok, report.to_dict()
    assert not report.fired
    assert report.expectation_met is True
    # Either a pass finding or no finding at all is acceptable here —
    # what matters is that no open finding fired.
    assert all(f.status.value != "open" for f in report.findings)


def test_expectation_failure_flips_ok_to_false():
    report = run_rule_against_fixture(
        rule_path=VALID_RULES / "cross_doc_weight_match.yaml",
        fixture_path=EXTRACTION_FIXTURES / "bpcr_weight_match.json",
        repo_root=REPO_ROOT,
        expected="fires",  # lie — the fixture matches so the rule is silent
    )
    assert report.expectation_met is False
    assert report.ok is False


def test_invalid_rule_surfaces_schema_errors_without_running():
    report = run_rule_against_fixture(
        rule_path=INVALID_RULES / "missing_schema_version.yaml",
        fixture_path=EXTRACTION_FIXTURES / "bpcr_weight_match.json",
        repo_root=REPO_ROOT,
    )
    assert report.ok is False
    assert report.validation is not None
    assert report.validation.ok is False
    assert report.findings == []


def test_missing_fixture_returns_actionable_error(tmp_path: Path):
    report = run_rule_against_fixture(
        rule_path=VALID_RULES / "cross_doc_weight_match.yaml",
        fixture_path=tmp_path / "does_not_exist.json",
        repo_root=REPO_ROOT,
    )
    assert report.ok is False
    assert any("fixture file not found" in e.message for e in report.errors)


def test_malformed_fixture_is_reported(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not: valid json", encoding="utf-8")
    report = run_rule_against_fixture(
        rule_path=VALID_RULES / "cross_doc_weight_match.yaml",
        fixture_path=bad,
        repo_root=REPO_ROOT,
    )
    assert report.ok is False
    assert any(
        "not a valid ExtractedPackage JSON" in e.message for e in report.errors
    )


def test_missing_aliases_file_blocks_run(tmp_path: Path):
    rule_text = (VALID_RULES / "cross_doc_weight_match.yaml").read_text()
    # Re-point the aliases_file at something the repo doesn't have.
    rule_text = rule_text.replace(
        "backend/config/rules/pilot/aliases/materials.yaml",
        "backend/config/rules/pilot/aliases/does_not_exist.yaml",
    )
    patched = tmp_path / "broken.yaml"
    patched.write_text(rule_text, encoding="utf-8")

    report = run_rule_against_fixture(
        rule_path=patched,
        fixture_path=EXTRACTION_FIXTURES / "bpcr_weight_mismatch.json",
        repo_root=REPO_ROOT,
    )
    assert report.ok is False
    assert any("aliases_file" in e.path for e in report.errors)
    # The evaluator must not run with unresolved aliases.
    assert report.findings == []


def test_to_dict_round_trips_for_json_output():
    report = run_rule_against_fixture(
        rule_path=VALID_RULES / "cross_doc_weight_match.yaml",
        fixture_path=EXTRACTION_FIXTURES / "bpcr_weight_mismatch.json",
        repo_root=REPO_ROOT,
    )
    payload = report.to_dict()
    assert payload["rule_id"] == report.rule_id
    assert payload["fired"] is True
    assert payload["findings"]
    assert "evidence" in payload["findings"][0]
    for entry in payload["findings"][0]["evidence"]:
        assert set(entry) == {"doc_id", "page_index", "field", "value", "note"}
