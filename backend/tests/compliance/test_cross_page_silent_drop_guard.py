"""Pin the cross-page LLM silent-drop guard.

Run e5e35ffc-… (2026-05-12) audit surfaced this: REC-MAN2
(``manufacturing_step_traceability``) was sent to the cross-page
LLM but the response only included REC-MAT1 and REC-IN_3. The
``RuleBatchResult`` had 2 evaluations for a 3-rule prompt and
the evaluator returned that partial result as-is. REC-MAN2
vanished from ``agent_reports[reconciliation].all_evaluations``
with no log line, no telemetry event, no error — a regulator-
visible cross-document rule silently lost its verdict.

This test pins the guard added in this branch: any requested
rule_id missing from the LLM's response must be backfilled with
``status="error"`` AND a ``cross_page.rule_dropped_by_llm``
telemetry event must fire so the drop is visible in
``telemetry.json``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.compliance.cross_page.evaluator import CrossPageEvaluator
from app.compliance.models import RuleBatchResult, RuleEvaluation
from app.compliance.rules.registry import AuditRule


def _rule(rid: str) -> AuditRule:
    return AuditRule(
        id=rid,
        number=1,
        category="reconciliation",
        category_display="Reconciliation",
        agent="reconciliation",
        text="dummy",
    )


@pytest.mark.asyncio
async def test_silent_drop_backfilled_as_error_with_telemetry() -> None:
    """LLM returns only 2 of the 3 requested rule_ids. The evaluator
    must backfill the missing one as status='error' and emit the
    drop telemetry event so the operator sees REC-MAN2 didn't
    silently vanish."""

    async def fake_generate_structured(prompt, schema, system=None):  # noqa: ARG001
        # Intentionally omit REC-MAN2 — mirrors the prod silent-drop.
        return RuleBatchResult(evaluations=[
            RuleEvaluation(rule_id="REC-MAT1", status="non_compliant", description="x"),
            RuleEvaluation(rule_id="REC-IN_3", status="compliant", description="y"),
        ])

    fake_llm = AsyncMock()
    fake_llm.generate_structured = fake_generate_structured

    evaluator = CrossPageEvaluator(fake_llm)
    rules = [_rule("REC-MAT1"), _rule("REC-MAN2"), _rule("REC-IN_3")]
    sections = {"sec_a": "content a", "sec_b": "content b"}
    meta = {
        "sec_a": {"name": "A", "section_type": "x", "start_page": 1, "end_page": 1},
        "sec_b": {"name": "B", "section_type": "y", "start_page": 2, "end_page": 2},
    }

    result = await evaluator.evaluate(rules, sections, meta, dependency_tags=None)

    by_id = {ev.rule_id: ev for ev in result.evaluations}
    assert set(by_id.keys()) == {"REC-MAT1", "REC-MAN2", "REC-IN_3"}, (
        "silent-drop guard didn't backfill the missing rule"
    )
    assert by_id["REC-MAN2"].status == "error"
    assert "silently dropped" in by_id["REC-MAN2"].description.lower() or \
           "omitted" in by_id["REC-MAN2"].description.lower(), (
        "backfill description must explain the drop so operators "
        "don't mistake it for a real evaluation error"
    )
    # Rules the LLM DID return must come back unchanged.
    assert by_id["REC-MAT1"].status == "non_compliant"
    assert by_id["REC-IN_3"].status == "compliant"


@pytest.mark.asyncio
async def test_complete_response_passes_through_untouched() -> None:
    """When the LLM returns every requested rule_id, the evaluator
    must not synthesize spurious backfills."""

    async def fake_generate_structured(prompt, schema, system=None):  # noqa: ARG001
        return RuleBatchResult(evaluations=[
            RuleEvaluation(rule_id="REC-MAT1", status="non_compliant", description="x"),
            RuleEvaluation(rule_id="REC-MAN2", status="compliant", description="ok"),
            RuleEvaluation(rule_id="REC-IN_3", status="compliant", description="y"),
        ])

    fake_llm = AsyncMock()
    fake_llm.generate_structured = fake_generate_structured

    evaluator = CrossPageEvaluator(fake_llm)
    rules = [_rule("REC-MAT1"), _rule("REC-MAN2"), _rule("REC-IN_3")]
    sections = {"sec_a": "content"}
    meta = {"sec_a": {"name": "A", "section_type": "x", "start_page": 1, "end_page": 1}}

    result = await evaluator.evaluate(rules, sections, meta, dependency_tags=None)

    assert len(result.evaluations) == 3
    statuses = {ev.rule_id: ev.status for ev in result.evaluations}
    assert statuses == {
        "REC-MAT1": "non_compliant",
        "REC-MAN2": "compliant",
        "REC-IN_3": "compliant",
    }
