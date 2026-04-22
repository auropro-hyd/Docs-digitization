"""Tests for :mod:`app.bmr.rules.diff` (Spec 005 authoring loop).

The diff module is consumed both by the ``bmr-rules diff`` CLI and by
the ``bmr-rule-author`` skill's tune-mode report. These tests pin the
behaviour both callers rely on:

- Unchanged inputs produce an empty report.
- Tuning hot-spots (tolerance, alias files, severity, scope,
  synthesis sources) are tagged with the right :class:`ChangeTag`.
- Lists are treated as atomic values rather than positional diffs.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.bmr.rules.diff import (
    ChangeKind,
    ChangeTag,
    diff_rule_files,
    diff_rule_mappings,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "rules"


def _load(name: str) -> dict:
    return yaml.safe_load((FIXTURES / "valid" / name).read_text(encoding="utf-8"))


def test_identical_rules_produce_no_changes():
    left = _load("cross_doc_weight_match.yaml")
    right = _load("cross_doc_weight_match.yaml")
    report = diff_rule_mappings(left, right)
    assert not report.has_changes
    assert report.to_dict()["entries"] == []


def test_tolerance_relaxation_is_tagged():
    left = _load("cross_doc_weight_match.yaml")
    right = yaml.safe_load(yaml.safe_dump(left))  # deep-ish copy
    right["tolerance"]["value"] = 0.5  # was 0.1

    report = diff_rule_mappings(left, right)
    assert report.has_changes
    entry = next(e for e in report.entries if e.path == "/tolerance/value")
    assert entry.kind is ChangeKind.CHANGED
    assert ChangeTag.TOLERANCE_RELAXED in entry.tags
    assert ChangeTag.TOLERANCE_RELAXED in report.tags


def test_tolerance_tightening_is_tagged():
    left = _load("cross_doc_weight_match.yaml")
    right = yaml.safe_load(yaml.safe_dump(left))
    right["tolerance"]["value"] = 0.05

    report = diff_rule_mappings(left, right)
    entry = next(e for e in report.entries if e.path == "/tolerance/value")
    assert ChangeTag.TOLERANCE_TIGHTENED in entry.tags


def test_severity_change_and_version_bump_are_tagged():
    left = _load("cross_doc_weight_match.yaml")
    right = yaml.safe_load(yaml.safe_dump(left))
    right["severity"] = "minor"
    right["version"] = "1.1.0"

    report = diff_rule_mappings(left, right)
    tags_by_path = {e.path: e.tags for e in report.entries}
    assert ChangeTag.SEVERITY_CHANGED in tags_by_path["/severity"]
    assert ChangeTag.VERSION_BUMPED in tags_by_path["/version"]


def test_alias_file_change_is_tagged():
    left = _load("cross_doc_weight_match.yaml")
    right = yaml.safe_load(yaml.safe_dump(left))
    right["context_object"]["entity_match"]["aliases_file"] = (
        "backend/config/rules/pilot/aliases/materials_v2.yaml"
    )

    report = diff_rule_mappings(left, right)
    entry = next(
        e
        for e in report.entries
        if e.path.endswith("/entity_match/aliases_file")
    )
    assert ChangeTag.ALIAS_FILE_CHANGED in entry.tags


def test_scope_change_is_tagged():
    left = _load("cross_doc_weight_match.yaml")
    right = yaml.safe_load(yaml.safe_dump(left))
    right["context_object"]["scope"] = "page_aggregate"

    report = diff_rule_mappings(left, right)
    entry = next(
        e for e in report.entries if e.path == "/context_object/scope"
    )
    assert ChangeTag.SCOPE_CHANGED in entry.tags


def test_list_treated_as_atomic():
    left = _load("cross_doc_weight_match.yaml")
    right = yaml.safe_load(yaml.safe_dump(left))
    right["context_object"]["entity_match"]["punctuation_strip"] = [
        "-",
        ".",
        ",",
    ]

    report = diff_rule_mappings(left, right)
    entries = [
        e for e in report.entries if "punctuation_strip" in e.path
    ]
    assert len(entries) == 1
    assert entries[0].kind is ChangeKind.CHANGED


def test_added_and_removed_keys():
    left = _load("cross_doc_weight_match.yaml")
    right = yaml.safe_load(yaml.safe_dump(left))
    right["gmp_category"] = "Data Integrity"  # new key
    right.pop("fallback", None)  # removed key

    report = diff_rule_mappings(left, right)
    by_path = {e.path: e for e in report.entries}
    assert by_path["/gmp_category"].kind is ChangeKind.ADDED
    assert by_path["/gmp_category"].right == "Data Integrity"
    assert by_path["/fallback"].kind is ChangeKind.REMOVED
    assert by_path["/fallback"].left == "flag_as_unevaluated"


def test_diff_rule_files_loads_from_disk(tmp_path: Path):
    base = _load("cross_doc_weight_match.yaml")
    candidate = yaml.safe_load(yaml.safe_dump(base))
    candidate["tolerance"]["value"] = 0.2
    candidate["version"] = "1.1.0"
    left = tmp_path / "v1.yaml"
    right = tmp_path / "v2.yaml"
    left.write_text(yaml.safe_dump(base), encoding="utf-8")
    right.write_text(yaml.safe_dump(candidate), encoding="utf-8")

    report = diff_rule_files(left, right)
    assert report.left_version == "1.0.0"
    assert report.right_version == "1.1.0"
    assert ChangeTag.TOLERANCE_RELAXED in report.tags
    assert ChangeTag.VERSION_BUMPED in report.tags
