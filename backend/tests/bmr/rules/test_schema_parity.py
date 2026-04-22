"""Schema parity guard (Spec 005 plan.md, Post-Design Constitution Re-Check §VII).

The backend rule loader (consumed by the compliance pipeline stage) and
the authoring-side validator (consumed by the ``bmr-rules`` CLI and the
``bmr-rule-author`` skill) MUST share exactly one JSON Schema file per
version. If a future refactor splits the schema across two locations,
rules that validate in one pathway might silently fail in the other —
which is precisely the footgun versioned schemas are meant to prevent.

These tests assert the single-source-of-truth invariant end-to-end:

1. Every advertised schema version resolves to one physical file.
2. The workflow pipeline and the authoring CLI load the *same* schema
   object for a given version.
3. A canonical valid rule validates identically via
   :func:`app.bmr.rules.validator.validate_rule_mapping` (authoring
   path) and :func:`app.bmr.rules.loader.load_rule_file` (runtime
   loader path).
4. A deliberately broken rule produces identical blocking errors via
   both pathways.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from app.bmr.rules.loader import load_rule_file
from app.bmr.rules.schema import (
    available_schema_versions,
    default_schema_dir,
    load_schema,
    schema_path_for,
)
from app.bmr.rules.validator import validate_rule_mapping

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "rules"


def test_each_advertised_version_has_exactly_one_schema_file():
    schema_dir = default_schema_dir()
    for version in available_schema_versions():
        path = schema_path_for(version)
        assert path.parent == schema_dir, (
            f"schema for {version} is outside the canonical directory "
            f"{schema_dir}; got {path}"
        )
        siblings = list(schema_dir.glob(f"rule.schema.v{version}.json"))
        assert len(siblings) == 1, (
            f"expected exactly one schema file for {version}, "
            f"found {len(siblings)}: {siblings}"
        )


def test_load_schema_is_deterministic_across_paths():
    # ``load_schema`` normalises through the cache; a fresh read of the
    # same file must deep-equal the cached value.
    for version in available_schema_versions():
        cached = load_schema(version)
        fresh = json.loads(schema_path_for(version).read_text(encoding="utf-8"))
        assert cached == fresh, (
            f"load_schema({version!r}) drifted from the on-disk JSON; "
            "the cache keying may be broken."
        )


def test_authoring_and_loader_agree_on_valid_rule():
    rule_path = FIXTURES / "valid" / "cross_doc_weight_match.yaml"
    mapping = yaml.safe_load(rule_path.read_text(encoding="utf-8"))

    direct = validate_rule_mapping(mapping, source_path=rule_path)
    loaded, loader_report = load_rule_file(rule_path)

    assert direct.ok, direct.to_dict()
    assert loader_report.ok, loader_report.to_dict()
    assert loaded is not None
    assert direct.to_dict() == loader_report.to_dict()


def test_authoring_and_loader_agree_on_invalid_rule():
    rule_path = FIXTURES / "invalid" / "missing_schema_version.yaml"
    mapping = yaml.safe_load(rule_path.read_text(encoding="utf-8"))

    direct = validate_rule_mapping(mapping, source_path=rule_path)
    loaded, loader_report = load_rule_file(rule_path)

    assert loaded is None
    assert not direct.ok
    assert not loader_report.ok
    # Both pathways must produce the same error set — not just "both
    # failed". If the shapes diverge, authors would see one message in
    # the CLI and a different one at runtime, which is the user
    # experience this test exists to prevent.
    direct_errors = [(e.path, e.message) for e in direct.errors]
    loader_errors = [(e.path, e.message) for e in loader_report.errors]
    assert direct_errors == loader_errors
