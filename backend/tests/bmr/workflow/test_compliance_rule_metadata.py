"""Compliance-stage tests for Spec 005 rule metadata.

Covers two tight contracts:

- FR-005: every finding emitted by the stage carries the loader's
  ``content_hash``. Capabilities don't know about loading, so the stage
  is responsible for stamping — a regression here would silently drop
  the reproducibility fingerprint from audit trails.

- FR-013: rules with ``deprecated: true`` are loaded (so prior runs
  that reference them still resolve) but skipped by the compliance
  stage. The stage returns a separate ``rules_skipped_deprecated``
  counter so the REPORT stage can surface it to reviewers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.bmr.capabilities.evidence import (
    EvidenceRegion,
    FindingDraft,
    FindingSource,
    FindingStatus,
)
from app.bmr.capabilities.extracted_data import ExtractedPackage
from app.bmr.workflow import stages as stages_mod


def _probe_rule_body(rid: str) -> dict:
    return {
        "id": rid,
        "version": "1.0.0",
        "severity": "minor",
        "alcoa_tag": "Accurate",
        "description": "probe",
        "context_object": {"scope": "same_page"},
        "source": {"field": "probe", "scope_hint": "bpcr_step_page"},
    }


class _StubRule:
    def __init__(
        self, rule_id: str, *, deprecated: bool = False, content_hash: str = ""
    ) -> None:
        self.rule = _probe_rule_body(rule_id)
        self.id = rule_id
        self.source_path = f"stub::{rule_id}"
        self.schema_version = "1.0"
        self.content_hash = content_hash or f"hash-{rule_id}"
        self.deprecated = deprecated
        self.superseded_by = None

    @property
    def scope(self) -> str:
        return "same_page"

    @property
    def version(self) -> str:
        return "1.0.0"


def _passthrough_eval(*, rule, extracted, alias_tables):  # type: ignore[no-untyped-def]
    del extracted, alias_tables
    return [
        FindingDraft(
            rule_id=rule["id"],
            rule_version="1.0.0",
            status=FindingStatus.PASS,
            severity="minor",
            source=FindingSource.ALCOA,
            summary=f"{rule['id']} ok",
            evidence=[EvidenceRegion(doc_id="d", page_index=1)],
        )
    ]


def _install_bank(monkeypatch: pytest.MonkeyPatch, rules: list[_StubRule]) -> None:
    class _StubBank:
        ok = True
        errors: list = []

        def __init__(self) -> None:
            self.rules = rules

    monkeypatch.setitem(stages_mod._DISPATCH, "same_page", _passthrough_eval)
    monkeypatch.setattr(stages_mod, "load_rule_bank", lambda path: _StubBank())


def test_compliance_stamps_content_hash_on_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rule = _StubRule("probe.rule.hashed", content_hash="ab" * 32)
    _install_bank(monkeypatch, [rule])

    compliance = stages_mod.make_compliance_stage(repo_root=tmp_path)
    state = {
        "rules_dir": str(tmp_path),
        "extracted": ExtractedPackage(package_id="pkg"),
        "package_id": "pkg",
    }

    out = compliance(state)  # type: ignore[arg-type]

    assert len(out["findings"]) == 1
    assert out["findings"][0].rule_content_hash == "ab" * 32


def test_compliance_skips_deprecated_rules_and_reports_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    active = _StubRule("probe.rule.active")
    retired = _StubRule("probe.rule.retired", deprecated=True)
    _install_bank(monkeypatch, [active, retired])

    compliance = stages_mod.make_compliance_stage(repo_root=tmp_path)
    state = {
        "rules_dir": str(tmp_path),
        "extracted": ExtractedPackage(package_id="pkg"),
        "package_id": "pkg",
    }

    out = compliance(state)  # type: ignore[arg-type]

    # Only the active rule fires — the deprecated one is inert but
    # still counted so operators can audit the skip.
    assert [f.rule_id for f in out["findings"]] == ["probe.rule.active"]
    assert out["rules_evaluated"] == 1
    assert out["rules_loaded"] == 2
    assert out["rules_skipped_deprecated"] == 1


def test_compliance_counts_when_no_rules_deprecated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rules = [_StubRule(f"probe.rule.{n}") for n in range(3)]
    _install_bank(monkeypatch, rules)

    compliance = stages_mod.make_compliance_stage(repo_root=tmp_path)
    out = compliance(  # type: ignore[arg-type]
        {
            "rules_dir": str(tmp_path),
            "extracted": ExtractedPackage(package_id="pkg"),
            "package_id": "pkg",
        }
    )

    assert out["rules_evaluated"] == 3
    assert out["rules_loaded"] == 3
    assert out["rules_skipped_deprecated"] == 0
