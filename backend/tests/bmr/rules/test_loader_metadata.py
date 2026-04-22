"""Loader metadata tests — Spec 005 FR-005 (content hash) + FR-013 (deprecation).

The loader is responsible for two pieces of metadata the rest of the
pipeline relies on but never re-computes:

1. A deterministic ``content_hash`` over the canonical rule body. Every
   finding produced by a loaded rule carries this hash so a prior run
   can be replayed and checked byte-for-byte. If authors tweak a rule
   without bumping semver, the hash still tells reviewers the bodies
   differ.

2. A ``deprecated`` flag (+ optional ``superseded_by`` pointer). The
   loader accepts deprecated rules — so old finding rows continue to
   resolve — but the compliance stage skips them.

These tests exercise both pathways at the unit level. Stage-level
integration lives in ``tests/bmr/workflow/test_compliance_parallel.py``
and the e2e suite.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.bmr.rules.loader import compute_rule_content_hash, load_rule_bank, load_rule_file

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "rules"


def _write_valid_rule(path: Path, *, extra: dict | None = None) -> None:
    # Using a non-numeric-looking field name so the validator's
    # "numeric fields must declare a tolerance" guard does not trip —
    # these tests are about loader metadata, not tolerance logic.
    base = {
        "schema_version": "1.0",
        "id": "alcoa.attributable.operator-signature-present",
        "version": "1.0.0",
        "severity": "critical",
        "alcoa_tag": "Attributable",
        "description": "Every executed step page must record an operator signature.",
        "context_object": {"scope": "same_page"},
        "source": {"field": "operator_signature", "scope_hint": "bpcr_step_page"},
    }
    if extra:
        base.update(extra)
    path.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")


# ── FR-005: content hash ─────────────────────────────────────────────────────


def test_content_hash_is_deterministic_across_reloads(tmp_path: Path) -> None:
    rule_path = tmp_path / "rule.yaml"
    _write_valid_rule(rule_path)

    first, _ = load_rule_file(rule_path)
    second, _ = load_rule_file(rule_path)

    assert first is not None and second is not None
    # Same bytes on disk must hash identically regardless of object
    # identity, dict ordering, or YAML round-trip quirks.
    assert first.content_hash == second.content_hash
    assert len(first.content_hash) == 64  # sha256 hex length


def test_content_hash_changes_when_body_changes(tmp_path: Path) -> None:
    rule_path = tmp_path / "rule.yaml"
    _write_valid_rule(rule_path)
    original, _ = load_rule_file(rule_path)

    # Same semver, different body (swap severity). This is exactly the
    # scenario FR-005 exists to catch — the author forgot to bump
    # ``version`` but the rule behaviour changed.
    _write_valid_rule(rule_path, extra={"severity": "major"})
    updated, _ = load_rule_file(rule_path)

    assert original is not None and updated is not None
    assert original.version == updated.version == "1.0.0"
    assert original.content_hash != updated.content_hash, (
        "content_hash must reflect body changes even when semver is unchanged"
    )


def test_content_hash_stable_under_key_reordering(tmp_path: Path) -> None:
    rule_a = tmp_path / "a.yaml"
    rule_b = tmp_path / "b.yaml"

    body = {
        "schema_version": "1.0",
        "id": "alcoa.attributable.stable-sort",
        "version": "1.0.0",
        "severity": "minor",
        "alcoa_tag": "Attributable",
        "description": "Key order must not affect the content hash.",
        "context_object": {"scope": "same_page"},
        "source": {"field": "operator_signature", "scope_hint": "bpcr_step_page"},
    }
    rule_a.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    # Re-serialise with sorted keys: YAML text differs, semantic content
    # is identical. A correct canonical hash ignores key order.
    rule_b.write_text(yaml.safe_dump(body, sort_keys=True), encoding="utf-8")

    a, _ = load_rule_file(rule_a)
    b, _ = load_rule_file(rule_b)

    assert a is not None and b is not None
    assert a.content_hash == b.content_hash


def test_compute_rule_content_hash_ignores_injected_metadata() -> None:
    body = {
        "schema_version": "1.0",
        "id": "alcoa.attributable.ignored-injected",
        "version": "1.0.0",
        "severity": "minor",
        "alcoa_tag": "Attributable",
        "description": "Injected loader metadata must not perturb the hash.",
        "context_object": {"scope": "same_page"},
        "source": {"field": "operator_signature", "scope_hint": "bpcr_step_page"},
    }
    # Simulating what the loader might inject — ``source_path`` /
    # ``content_hash`` columns — must leave the hash invariant.
    polluted = {**body, "source_path": "/tmp/fake.yaml", "content_hash": "deadbeef"}

    assert compute_rule_content_hash(body) == compute_rule_content_hash(polluted)


def test_stamped_version_glues_semver_and_hash_prefix(tmp_path: Path) -> None:
    rule_path = tmp_path / "rule.yaml"
    _write_valid_rule(rule_path)
    loaded, _ = load_rule_file(rule_path)

    assert loaded is not None
    assert loaded.stamped_version == f"{loaded.version}+{loaded.content_hash[:12]}"


# ── FR-013: deprecation ──────────────────────────────────────────────────────


def test_loader_accepts_deprecated_rule(tmp_path: Path) -> None:
    rule_path = tmp_path / "dep.yaml"
    _write_valid_rule(
        rule_path,
        extra={
            "deprecated": True,
            "superseded_by": "alcoa.attributable.operator-signature-present@2.0.0",
        },
    )

    loaded, report = load_rule_file(rule_path)

    assert report.ok, report.to_dict()
    assert loaded is not None
    assert loaded.deprecated is True
    assert (
        loaded.superseded_by
        == "alcoa.attributable.operator-signature-present@2.0.0"
    )


def test_loader_defaults_deprecated_false_when_field_missing(tmp_path: Path) -> None:
    rule_path = tmp_path / "active.yaml"
    _write_valid_rule(rule_path)

    loaded, _ = load_rule_file(rule_path)

    assert loaded is not None
    assert loaded.deprecated is False
    assert loaded.superseded_by is None


def test_rule_bank_loads_mixed_active_and_deprecated(tmp_path: Path) -> None:
    _write_valid_rule(
        tmp_path / "active.yaml",
        extra={"id": "alcoa.attributable.active-one"},
    )
    _write_valid_rule(
        tmp_path / "retired.yaml",
        extra={
            "id": "alcoa.attributable.retired-one",
            "deprecated": True,
            "superseded_by": "alcoa.attributable.active-one@1.0.0",
        },
    )

    bank = load_rule_bank(tmp_path)

    assert bank.ok, bank.errors
    ids = {loaded.id: loaded for loaded in bank.rules}
    assert ids["alcoa.attributable.active-one"].deprecated is False
    assert ids["alcoa.attributable.retired-one"].deprecated is True
