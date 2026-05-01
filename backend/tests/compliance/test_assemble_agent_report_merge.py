"""Regression: merged RuleResult reasoning must match the winning status."""

from __future__ import annotations

from app.compliance.evaluator import assemble_agent_report
from app.compliance.models import RuleBatchResult, RuleEvaluation
from app.compliance.rules.registry import AuditRule


def _rule(rule_id: str = "GMP-PRE5") -> AuditRule:
    return AuditRule(
        id=rule_id,
        number=5,
        category="pre_manufacturing_checks",
        category_display="Pre-Manufacturing Checks",
        agent="gmp",
        text="Complete pre-start checklist.",
    )


def test_reasoning_follows_status_when_na_then_compliant() -> None:
    """Earlier page gate-skip must not leave stale skip text once a later page is compliant."""
    rule = _rule()
    na = RuleEvaluation(
        rule_id=rule.id,
        status="not_applicable",
        confidence=1.0,
        reasoning=(
            "Document type 'batch_record' is not in applicable types "
            "['operation_checklist'] for this rule"
        ),
    )
    ok = RuleEvaluation(
        rule_id=rule.id,
        status="compliant",
        confidence=0.92,
        reasoning="Operator signed the pre-start checklist; dates recorded for each line.",
        evidence="Pre-start checklist: Signed by J. Doe 12/03/2025",
    )
    batch_results = [
        ("batch-a", 1, RuleBatchResult(evaluations=[na])),
        ("batch-a", 2, RuleBatchResult(evaluations=[ok])),
    ]
    report = assemble_agent_report("gmp", [rule], batch_results, [1, 2])
    assert len(report.all_evaluations) == 1
    merged = report.all_evaluations[0]
    assert merged.status == "compliant"
    assert "pre-start checklist" in merged.reasoning.lower()
    assert "not in applicable types" not in merged.reasoning
    assert set(merged.page_numbers) == {1, 2}
    assert "Signed by J. Doe" in (merged.evidence or "")


def test_reasoning_follows_status_when_compliant_then_non_compliant() -> None:
    rule = _rule("GMP-RAW8")
    ok = RuleEvaluation(
        rule_id=rule.id,
        status="compliant",
        confidence=0.9,
        reasoning="Dispensing weights match BOM line items.",
    )
    bad = RuleEvaluation(
        rule_id=rule.id,
        status="non_compliant",
        confidence=0.88,
        reasoning="Material X weight missing second witness signature.",
        evidence="Lot ABC — witness column blank",
    )
    batch_results = [
        ("b1", 3, RuleBatchResult(evaluations=[ok])),
        ("b1", 4, RuleBatchResult(evaluations=[bad])),
    ]
    report = assemble_agent_report("gmp", [rule], batch_results, [3, 4])
    merged = report.all_evaluations[0]
    assert merged.status == "non_compliant"
    assert "witness" in merged.reasoning.lower()
    assert "BOM" not in merged.reasoning  # stale compliant text dropped


def test_compliant_reasoning_kept_when_later_page_is_na() -> None:
    """Lower-severity later eval must not overwrite status or narrative."""
    rule = _rule("GMP-EQU10")
    ok = RuleEvaluation(
        rule_id=rule.id,
        status="compliant",
        confidence=0.9,
        reasoning="Deviation register cross-referenced on this page.",
    )
    na = RuleEvaluation(
        rule_id=rule.id,
        status="not_applicable",
        confidence=1.0,
        reasoning="Section type 'cover_page' is not in applicable types ...",
    )
    batch_results = [
        ("b1", 1, RuleBatchResult(evaluations=[ok])),
        ("b1", 2, RuleBatchResult(evaluations=[na])),
    ]
    report = assemble_agent_report("gmp", [rule], batch_results, [1, 2])
    merged = report.all_evaluations[0]
    assert merged.status == "compliant"
    assert "Deviation register" in merged.reasoning
