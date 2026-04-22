"""The COMPLIANCE stage fan-out runs leaf rules concurrently.

We prove this by injecting a capability whose evaluator blocks on a
shared ``threading.Barrier``. If the fan-out were sequential the test
would deadlock; with the thread-pool it releases once the barrier sees
all rules.
"""

from __future__ import annotations

import threading
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


def _probe_rule(rid: str) -> dict:
    return {
        "id": rid,
        "version": "1.0.0",
        "severity": "minor",
        "alcoa_tag": "Accurate",
        "description": "probe",
        "context_object": {"scope": "same_page"},
        "source": {"field": "probe", "scope_hint": "bpcr_step_page"},
    }


class _ProbeRule:
    def __init__(self, rule_id: str) -> None:
        self.rule = _probe_rule(rule_id)
        self.id = rule_id
        self.source_path = f"probe::{rule_id}"
        self.schema_version = "1.0"
        self.content_hash = f"probehash{rule_id}"
        self.deprecated = False
        self.superseded_by = None

    @property
    def scope(self) -> str:
        return "same_page"

    @property
    def version(self) -> str:
        return "1.0.0"


def test_leaf_rules_evaluate_in_parallel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    rules = [_ProbeRule(f"probe.rule.{n}") for n in range(4)]
    barrier = threading.Barrier(len(rules), timeout=5)

    def barrier_eval(*, rule, extracted, alias_tables):  # type: ignore[no-untyped-def]
        del extracted, alias_tables
        barrier.wait()
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

    monkeypatch.setitem(stages_mod._DISPATCH, "same_page", barrier_eval)

    class _StubBank:
        ok = True
        errors: list = []

        def __init__(self) -> None:
            self.rules = rules

    monkeypatch.setattr(stages_mod, "load_rule_bank", lambda path: _StubBank())

    compliance = stages_mod.make_compliance_stage(
        repo_root=tmp_path, max_workers=len(rules)
    )
    state = {
        "rules_dir": str(tmp_path),
        "extracted": ExtractedPackage(package_id="pkg"),
        "package_id": "pkg",
    }
    out = compliance(state)  # type: ignore[arg-type]
    assert out["rules_evaluated"] == len(rules)
    assert len(out["findings"]) == len(rules)
    # bank ordering preserved in findings list
    assert [f.rule_id for f in out["findings"]] == [r.id for r in rules]
