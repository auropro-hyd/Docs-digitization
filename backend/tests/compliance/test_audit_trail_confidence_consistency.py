"""Pin the (status, reasoning, confidence) consistency contract on
audit-trail merges.

Background: when ``assemble_agent_report`` merges multiple per-page
evaluations of the same rule_id, severity escalates monotonically
(non_compliant > uncertain > compliant). Until this fix, reasoning
and evidence adopted the new eval's content on a severity upgrade
BUT confidence stayed at ``min(existing, new)``. The result was an
internally inconsistent record: a crisp non_compliant verdict (e.g.
0.9 confidence) reported with the older compliant eval's 0.4
confidence, making the finding look noisy when it wasn't.

This module pins the new policy:

  * on **severity upgrade** → the new eval drives the entire record
    (status, reasoning, evidence, AND confidence) — one coherent
    triple
  * on **no upgrade** (same or lower severity) → ``min`` semantics
    preserved for confidence, fill-if-empty for reasoning/evidence
"""

from __future__ import annotations

import pytest

from app.compliance.evaluator import assemble_agent_report
from app.compliance.models import RuleBatchResult, RuleEvaluation
from app.compliance.rules.registry import AuditRule


def _rule(rid: str = "CHK-1") -> AuditRule:
    return AuditRule(
        id=rid,
        number=1,
        category="checklist",
        category_display="Checklist",
        agent="checklist",
        text="dummy",
    )


def test_severity_upgrade_adopts_new_eval_confidence() -> None:
    """High-confidence non_compliant verdict must surface as
    non_compliant + high confidence, NOT non_compliant + low
    confidence from an earlier compliant pass on a different page."""

    rule = _rule()
    # Page 1: compliant 0.4 (noisy "looks fine" call)
    page1 = RuleBatchResult(evaluations=[RuleEvaluation(
        rule_id="CHK-1", status="compliant", confidence=0.4,
        reasoning="Quick scan, no obvious issues.",
    )])
    # Page 2: non_compliant 0.9 (crisp real finding)
    page2 = RuleBatchResult(evaluations=[RuleEvaluation(
        rule_id="CHK-1", status="non_compliant", confidence=0.9,
        reasoning="Operator signature missing on row 4.",
        evidence="Row 4 'Done by' cell is blank.",
    )])

    report = assemble_agent_report(
        agent="checklist", all_rules=[rule],
        batch_results=[("a", 1, page1), ("b", 2, page2)],
        pages_reviewed=[1, 2],
    )

    ev = next(e for e in report.all_evaluations if e.rule_id == "CHK-1")
    assert ev.status == "non_compliant"
    assert ev.confidence == pytest.approx(0.9), (
        f"on severity upgrade, confidence must adopt the new eval's "
        f"value (0.9) — old min() semantics returned "
        f"{ev.confidence}, which under-reports the verdict quality"
    )
    assert "signature missing" in ev.reasoning


def test_severity_upgrade_consistent_when_new_eval_low_confidence() -> None:
    """When the upgrading eval is itself low-confidence, the
    record's confidence reflects that — also internally consistent:
    a noisy non_compliant finding is reported as such, NOT
    artificially inflated."""

    rule = _rule()
    page1 = RuleBatchResult(evaluations=[RuleEvaluation(
        rule_id="CHK-1", status="compliant", confidence=0.95,
        reasoning="Looks fine.",
    )])
    page2 = RuleBatchResult(evaluations=[RuleEvaluation(
        rule_id="CHK-1", status="non_compliant", confidence=0.3,
        reasoning="Possibly something missing, hard to tell.",
    )])

    report = assemble_agent_report(
        agent="checklist", all_rules=[rule],
        batch_results=[("a", 1, page1), ("b", 2, page2)],
        pages_reviewed=[1, 2],
    )

    ev = next(e for e in report.all_evaluations if e.rule_id == "CHK-1")
    assert ev.status == "non_compliant"
    assert ev.confidence == pytest.approx(0.3), (
        "low-confidence non_compliant must report its own confidence — "
        "we want HITL to surface this for review, not auto-approve at 0.95"
    )


def test_no_upgrade_preserves_min_confidence_semantics() -> None:
    """When the new eval is SAME or LOWER severity, confidence
    aggregation falls back to ``min`` — we're capturing uncertainty
    across multiple same-severity passes, not promoting either as
    the verdict."""

    rule = _rule()
    # Both compliant on different pages, different confidences.
    page1 = RuleBatchResult(evaluations=[RuleEvaluation(
        rule_id="CHK-1", status="compliant", confidence=0.9,
        reasoning="Clean.",
    )])
    page2 = RuleBatchResult(evaluations=[RuleEvaluation(
        rule_id="CHK-1", status="compliant", confidence=0.5,
        reasoning="Lower-confidence agree.",
    )])

    report = assemble_agent_report(
        agent="checklist", all_rules=[rule],
        batch_results=[("a", 1, page1), ("b", 2, page2)],
        pages_reviewed=[1, 2],
    )

    ev = next(e for e in report.all_evaluations if e.rule_id == "CHK-1")
    assert ev.status == "compliant"
    assert ev.confidence == pytest.approx(0.5), (
        "same-severity merge must preserve min() — captures the "
        "noisier eval so HITL routing thresholds still see it"
    )


def test_lower_severity_eval_does_not_pull_verdict_confidence_down() -> None:
    """Sequence: compliant 0.6 → non_compliant 0.9 (upgrade) →
    compliant 0.3 (lower severity).

    The verdict at the end is non_compliant. The trailing compliant
    0.3 eval is NOT the verdict — it's a passing page on the same
    rule's coverage. It must NOT drag the verdict's confidence down
    via min(). Pre-fix: `existing.confidence = min(0.9, 0.3) = 0.3`
    silently shipped a "0.3 confidence non_compliant" record even
    though the actual verdict-driver was 0.9. Post-fix: lower-
    severity evals skip the confidence update entirely."""

    rule = _rule()
    evs = [
        RuleEvaluation(rule_id="CHK-1", status="compliant", confidence=0.6,
                       reasoning="P1 clean."),
        RuleEvaluation(rule_id="CHK-1", status="non_compliant", confidence=0.9,
                       reasoning="P2 finding."),
        RuleEvaluation(rule_id="CHK-1", status="compliant", confidence=0.3,
                       reasoning="P3 looks fine."),
    ]
    report = assemble_agent_report(
        agent="checklist", all_rules=[rule],
        batch_results=[
            (f"b{i}", i + 1, RuleBatchResult(evaluations=[e]))
            for i, e in enumerate(evs)
        ],
        pages_reviewed=[1, 2, 3],
    )

    ev = next(e for e in report.all_evaluations if e.rule_id == "CHK-1")
    assert ev.status == "non_compliant"
    assert ev.confidence == pytest.approx(0.9), (
        f"non_compliant verdict's confidence (0.9, from the verdict-"
        f"driving P2 eval) must NOT be dragged down by the trailing "
        f"P3 compliant 0.3 eval. Got {ev.confidence}."
    )
    # And the verdict's reasoning still belongs to the upgrading eval.
    assert "P2 finding" in ev.reasoning


def test_equal_severity_evals_aggregate_confidence_via_min() -> None:
    """Two non_compliant evals on the same rule, different pages.
    Both contribute to the verdict; confidence aggregation takes
    the noisier one to surface uncertainty to HITL routing."""

    rule = _rule()
    evs = [
        RuleEvaluation(rule_id="CHK-1", status="non_compliant", confidence=0.9,
                       reasoning="P1 finding."),
        RuleEvaluation(rule_id="CHK-1", status="non_compliant", confidence=0.5,
                       reasoning="P2 finding."),
    ]
    report = assemble_agent_report(
        agent="checklist", all_rules=[rule],
        batch_results=[
            (f"b{i}", i + 1, RuleBatchResult(evaluations=[e]))
            for i, e in enumerate(evs)
        ],
        pages_reviewed=[1, 2],
    )

    ev = next(e for e in report.all_evaluations if e.rule_id == "CHK-1")
    assert ev.status == "non_compliant"
    assert ev.confidence == pytest.approx(0.5), (
        f"same-severity evals must aggregate via min() to surface "
        f"the noisier one; got {ev.confidence}"
    )
