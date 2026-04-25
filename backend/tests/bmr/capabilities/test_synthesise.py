"""Tests for ``checklist_synthesise_v1``."""

from __future__ import annotations

from app.bmr.capabilities.evidence import (
    EvidenceRegion,
    FindingDraft,
    FindingSource,
    FindingStatus,
)
from app.bmr.capabilities.synthesise import checklist_synthesise_v1


def _leaf(
    rule_id: str,
    status: FindingStatus,
    *,
    severity: str = "major",
    page_index: int = 2,
    doc_id: str = "doc-bpcr",
) -> FindingDraft:
    return FindingDraft(
        rule_id=rule_id,
        rule_version="1.0.0",
        status=status,
        severity=severity,
        alcoa_tag="Accurate",
        gmp_category=None,
        summary=f"leaf {rule_id}",
        detail="",
        source=FindingSource.ALCOA,
        evidence=[EvidenceRegion(doc_id=doc_id, page_index=page_index, field="x")],
    )


def _synth_rule(group_by: str = "bpcr_step") -> dict:
    return {
        "id": "checklist.bpcr-step-complete.synthesis",
        "version": "1.0.0",
        "severity": "major",
        "alcoa_tag": "Complete",
        "context_object": {"scope": "checklist_synthesis", "group_by": group_by},
        "synthesises_from": ["rule.a", "rule.b"],
    }


def test_rolls_up_to_open_when_any_constituent_is_open():
    findings = [
        _leaf("rule.a", FindingStatus.OPEN, severity="critical", page_index=2),
        _leaf("rule.b", FindingStatus.PASS, severity="major", page_index=2),
    ]
    result = checklist_synthesise_v1(rule=_synth_rule(), findings=findings)
    assert len(result) == 1
    draft = result[0]
    assert draft.status is FindingStatus.OPEN
    assert draft.source is FindingSource.CHECKLIST_SYNTHESIS
    assert draft.severity == "critical"  # bumped to worst constituent
    assert draft.fields["open_count"] == 1
    assert draft.fields["total_constituents"] == 2
    assert draft.fields["group_ref"] == {"step_number": 2}


def test_rolls_up_to_pass_when_all_clean():
    findings = [
        _leaf("rule.a", FindingStatus.PASS, page_index=2),
        _leaf("rule.b", FindingStatus.PASS, page_index=2),
    ]
    result = checklist_synthesise_v1(rule=_synth_rule(), findings=findings)
    assert len(result) == 1
    assert result[0].status is FindingStatus.PASS
    assert result[0].fields["open_count"] == 0


def test_splits_into_one_roll_up_per_step():
    findings = [
        _leaf("rule.a", FindingStatus.OPEN, page_index=2),
        _leaf("rule.b", FindingStatus.PASS, page_index=2),
        _leaf("rule.a", FindingStatus.PASS, page_index=3),
        _leaf("rule.b", FindingStatus.PASS, page_index=3),
    ]
    result = checklist_synthesise_v1(rule=_synth_rule(), findings=findings)
    assert len(result) == 2
    statuses = {r.fields["group_ref"]["step_number"]: r.status for r in result}
    assert statuses[2] is FindingStatus.OPEN
    assert statuses[3] is FindingStatus.PASS


def test_ignores_findings_outside_synthesises_from():
    findings = [
        _leaf("rule.a", FindingStatus.OPEN, page_index=2),
        _leaf("rule.unrelated", FindingStatus.PASS, page_index=2),
    ]
    result = checklist_synthesise_v1(rule=_synth_rule(), findings=findings)
    assert len(result) == 1
    assert result[0].fields["constituent_rule_ids"] == ["rule.a"]


def test_unevaluated_when_no_constituents_matched():
    findings = [_leaf("rule.zzz", FindingStatus.OPEN)]
    result = checklist_synthesise_v1(rule=_synth_rule(), findings=findings)
    assert len(result) == 1
    assert result[0].status is FindingStatus.UNEVALUATED


def test_group_by_rule_emits_one_per_constituent():
    findings = [
        _leaf("rule.a", FindingStatus.OPEN, page_index=2),
        _leaf("rule.a", FindingStatus.PASS, page_index=3),
        _leaf("rule.b", FindingStatus.PASS, page_index=2),
    ]
    result = checklist_synthesise_v1(
        rule=_synth_rule(group_by="rule"), findings=findings
    )
    assert {r.fields["group_ref"]["constituent_rule_id"] for r in result} == {
        "rule.a",
        "rule.b",
    }
