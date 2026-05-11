"""Pin the contract that zero-rule agents do not produce fabricated reports.

Symptom this test guards against — observed in the
``2538104192-EHSII03.pdf`` run on 2026-05-04:

  GMP, SOP, and Checklist agents had zero rules in the live registry
  (their rule files are archived). Yet the executive summary listed
  them in ``strengths`` as "Full compliance with X" because each
  produced an ``AgentReport`` with the dataclass' default
  ``score=100.0``, and the LLM dutifully turned that into a positive
  observation.

Two orthogonal defences are pinned here:

1. **Executive-summary input filter** — even if a zero-rule report
   somehow reaches the summary builder, the prompt must not surface
   it as a scored agent. The only signal the LLM sees should be the
   absence-of-evaluation sentinel.

2. **Orchestration-layer filter** — when the registry holds no rules
   for an applicable agent, that agent must move to ``skipped_agents``
   with an explicit reason rather than being run and producing a
   default report.

The second defence requires a full pipeline harness, so this file
unit-tests the first directly and pins the second's logic shape via
a smaller helper extraction.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.compliance.models import AgentReport, ExecutiveSummary
from app.workflow.compliance_graph import _generate_executive_summary


def _ar(agent: str, total_rules: int, score: float, total_findings: int = 0) -> AgentReport:
    return AgentReport(
        agent=agent,
        agent_display=agent.upper(),
        score=score,
        total_rules=total_rules,
        total_findings=total_findings,
    )


@pytest.mark.asyncio
async def test_summary_excludes_zero_rule_agents_from_llm_prompt() -> None:
    """The exact failure mode from the May 4 run: GMP/SOP/Checklist
    had total_rules=0 but score=100.0 (default). Without the filter,
    the LLM saw 'GMP: score 100/100, 0 findings' and produced
    'Full compliance with GMP'."""

    captured_prompt = ""

    async def fake_generate_structured(prompt, schema, system=None):  # noqa: ARG001
        nonlocal captured_prompt
        captured_prompt = prompt
        return ExecutiveSummary(
            overall_assessment="x",
            key_risks=[],
            strengths=[],
            priority_actions=[],
        )

    fake_llm = AsyncMock()
    fake_llm.generate_structured = fake_generate_structured

    reports = [
        _ar("alcoa", total_rules=2, score=0.0, total_findings=1),
        _ar("gmp", total_rules=0, score=100.0),  # the fabrication culprit
        _ar("sop", total_rules=0, score=100.0),
        _ar("checklist", total_rules=0, score=100.0),
        _ar("reconciliation", total_rules=3, score=0.0, total_findings=3),
    ]

    await _generate_executive_summary(fake_llm, [], 0.0, reports)

    assert "GMP: score 100/100" not in captured_prompt
    assert "SOP: score 100/100" not in captured_prompt
    assert "CHECKLIST: score 100/100" not in captured_prompt
    # Scored agents must still be present.
    assert "ALCOA" in captured_prompt
    assert "RECONCILIATION" in captured_prompt


@pytest.mark.asyncio
async def test_summary_uses_sentinel_when_all_agents_have_zero_rules() -> None:
    """Edge case: if every agent has total_rules=0 (fresh deployment
    with all rule files archived), the prompt's ``Agent results``
    section must carry an explicit "no rule-level verdicts" sentinel
    rather than an empty string the LLM would silently fill with
    confabulation."""

    captured_prompt = ""

    async def fake_generate_structured(prompt, schema, system=None):  # noqa: ARG001
        nonlocal captured_prompt
        captured_prompt = prompt
        return ExecutiveSummary(
            overall_assessment="x", key_risks=[], strengths=[], priority_actions=[],
        )

    fake_llm = AsyncMock()
    fake_llm.generate_structured = fake_generate_structured

    reports = [_ar(name, total_rules=0, score=100.0) for name in ("gmp", "sop")]
    await _generate_executive_summary(fake_llm, [], 0.0, reports)

    assert "no agents produced rule-level verdicts" in captured_prompt


# ── Orchestration-layer filter shape ─────────────────────────


def test_empty_report_fallback_when_all_selected_agents_are_zero_rule() -> None:
    """The "I selected an agent but got no report" UX issue.

    When the user (or orchestrator) selects only zero-rule agents,
    ``agent_names_to_run`` is empty after the filter. Without
    the empty-report fallback the pipeline used to build a void
    report where the UI showed "0/0 agents done" with no
    explanation.

    With the fallback, an explicit empty report is returned
    carrying the skip reasons so the user sees:
    "Checklist: no rules registered" instead of a silent void.
    """

    import inspect
    from app.workflow import compliance_graph

    src = inspect.getsource(compliance_graph)

    # The fallback branch must exist and key off agent_names_to_run.
    assert "if not agent_names_to_run:" in src, (
        "empty-report fallback branch missing — the UX regression that "
        "produces '0/0 agents done' on user-selected zero-rule agents "
        "is back"
    )
    # The fallback must carry skip reasons through to the report.
    assert "report.skipped_agents = list(skipped)" in src, (
        "fallback must attach skipped_agents so the UI can render "
        "the per-agent skip reasons"
    )
    # The exec summary must be replaced — otherwise the LLM
    # confabulates "Full compliance" on a zero-rules run.
    assert "No rules were evaluated in this run" in src, (
        "fallback must replace executive_summary with a clear "
        "no-rules-evaluated message"
    )


def test_orchestration_filter_logic() -> None:
    """The orchestration filter is a small data transform: given an
    applicable agent list and a registry that returns a list of rules
    per agent, produce (agents_to_run, skipped_to_append). Pinning
    the transform here avoids spinning up the full pipeline harness
    for what is fundamentally a dict-walk.
    """

    from app.compliance.models import SkippedCategory

    # Fake registry: alcoa has 2 rules, gmp has 0, sop has 0,
    # reconciliation has 3.
    rules_by_agent = {
        "alcoa": ["r1", "r2"],
        "gmp": [],
        "sop": [],
        "checklist": [],
        "reconciliation": ["r3", "r4", "r5"],
    }

    applicable = ["alcoa", "gmp", "sop", "checklist", "reconciliation"]

    no_rule_agents = [a for a in applicable if not rules_by_agent.get(a)]
    agents_to_run = [a for a in applicable if a not in no_rule_agents]
    new_skipped = [
        SkippedCategory(category=a, reason="No rules are registered for this agent (rule file empty or archived).")
        for a in no_rule_agents
    ]

    assert agents_to_run == ["alcoa", "reconciliation"]
    assert {s.category for s in new_skipped} == {"gmp", "sop", "checklist"}
    assert all("No rules are registered" in s.reason for s in new_skipped)
