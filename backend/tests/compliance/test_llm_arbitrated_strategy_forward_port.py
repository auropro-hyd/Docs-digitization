"""Pin the 009-branch forward-port of text_primary / llm_arbitrated.

GMP rule 11 (final sign-off) on main declares
``evaluation_strategy: llm_arbitrated`` in ``gmp_rules.yaml:326``,
but until the 009-branch chain landed there was no
``_merge_llm_arbitrated`` function on main — the router fell
through to plain text-only evaluation and the strategy was silently
inert. This module pins:

  * the registry surfaces the new strategies cleanly,
  * GMP rule 11 actually carries ``llm_arbitrated`` so a future YAML
    refactor doesn't accidentally strip it,
  * the new ``compliance.unknown_evaluation_strategy`` warning fires
    when a rule declares a router-unknown strategy (the exact silent-
    degradation mode that hid this bug for weeks),
  * the new ``compliance.arbitration_fallback_used`` telemetry fires
    when arbitration raises OR when no LLM is provided, with the
    reason distinguishable.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest


# ── GMP rule 11 declaration ─────────────────────────────────────


def test_gmp_rule_11_declares_llm_arbitrated_strategy() -> None:
    """The whole point of the forward-port: rule 11's
    evaluation_strategy must remain ``llm_arbitrated`` in the loaded
    AuditRule. If a future refactor strips it, the rule silently
    degrades to plain text (the bug the forward-port fixed)."""

    from app.compliance.rules.registry import RuleRegistry

    r = RuleRegistry()
    gmp_rules = {rule.id: rule for rule in r.get_rules("gmp")}
    rule_11 = gmp_rules.get("GMP-EQU11")
    assert rule_11 is not None, (
        "GMP-EQU11 must exist after gmp_rules.yaml loads — registry "
        "regression"
    )
    assert rule_11.evaluation_strategy == "llm_arbitrated", (
        f"GMP-EQU11 must declare evaluation_strategy=llm_arbitrated; "
        f"got {rule_11.evaluation_strategy!r}. If the YAML changed "
        f"deliberately, update the test."
    )


# ── Unknown-strategy warning ───────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_strategy_logs_warning_and_records_telemetry(
    tmp_path, caplog,
) -> None:
    """A rule with an evaluation_strategy the router doesn't know
    must fall through to text BUT fire a warning + telemetry so the
    dangling strategy is visible. Without this, GMP-EQU11 was
    silently text-only for weeks."""

    from app.compliance.evaluator import run_agent_evaluation
    from app.compliance.models import RuleBatchResult, RuleEvaluation
    from app.compliance.rules.registry import AuditRule, RuleBatch
    from app.observability.run_telemetry import telemetry_run
    import logging

    rule = AuditRule(
        id="ANY-MADE-UP1",
        number=1,
        category="dummy",
        category_display="Dummy",
        agent="alcoa",
        text="dummy",
        evaluation_strategy="totally_made_up_strategy",  # router doesn't know this
    )
    batch = RuleBatch(
        batch_id="b1",
        category="dummy",
        agent="alcoa",
        rules=[rule],
    )
    section_map = {1: {"document_type": "batch_record", "section_type": "manufacturing_operations"}}
    extractions = [{"page_num": 1, "markdown": "x"}]

    async def fake_generate_structured(prompt, schema, system=None):
        return RuleBatchResult(evaluations=[
            RuleEvaluation(rule_id="ANY-MADE-UP1", status="compliant", confidence=0.9),
        ])

    fake_llm = AsyncMock()
    fake_llm.generate_structured = fake_generate_structured

    doc_id = "test-unknown-strategy"
    doc_dir = tmp_path / doc_id

    with caplog.at_level(logging.WARNING), telemetry_run(doc_id, doc_dir, name="test"):
        await run_agent_evaluation(
            agent="alcoa",
            batches=[batch],
            extractions=extractions,
            llm=fake_llm,
            max_concurrent=1,
            section_map=section_map,
        )

    # logger.warning landed in caplog
    assert any(
        "unknown_evaluation_strategy" in m or "totally_made_up_strategy" in m
        for m in caplog.messages
    ), (
        "router must logger.warning on unknown evaluation_strategy "
        "(it currently fell through silently)"
    )

    # And a structured telemetry event landed in telemetry.json
    telemetry_path = doc_dir / "telemetry-test.json"
    data = json.loads(telemetry_path.read_text(encoding="utf-8"))
    events = data.get("events", [])
    unknown_events = [
        e for e in events
        if e.get("event") == "compliance.unknown_evaluation_strategy"
    ]
    assert unknown_events, (
        "compliance.unknown_evaluation_strategy event not emitted"
    )
    assert unknown_events[0].get("fields", {}).get("strategy") == "totally_made_up_strategy"
    assert unknown_events[0].get("fields", {}).get("rule_id") == "ANY-MADE-UP1"


# ── Arbitration fallback telemetry ─────────────────────────────


@pytest.mark.asyncio
async def test_arbitration_fallback_telemetry_fires_when_llm_missing(
    tmp_path,
) -> None:
    """``_merge_llm_arbitrated`` falls back to higher-severity when
    no LLM is provided (a real deployment shape where the
    evaluator runs without an arbitrator LLM dependency). The
    ``compliance.arbitration_fallback_used`` event must fire with
    ``reason='no_llm_provided'`` so operators can distinguish
    that case from a broken arbitrator."""

    from app.compliance.evaluator import _merge_llm_arbitrated
    from app.compliance.models import RuleEvaluation
    from app.compliance.rules.registry import AuditRule
    from app.observability.run_telemetry import telemetry_run

    rule = AuditRule(
        id="GMP-EQU11",
        number=11,
        category="final_sign_off",
        category_display="Final Sign-off",
        agent="gmp",
        text="dummy",
        evaluation_strategy="llm_arbitrated",
    )
    # Conflicting text vs vision verdicts — forces the merge path.
    text_ev = RuleEvaluation(
        rule_id="GMP-EQU11", status="compliant", confidence=0.9,
        reasoning="text says fine",
    )
    vision_ev = RuleEvaluation(
        rule_id="GMP-EQU11", status="non_compliant", confidence=0.85,
        reasoning="vision sees blank signature",
    )

    doc_id = "test-arb-no-llm"
    doc_dir = tmp_path / doc_id

    with telemetry_run(doc_id, doc_dir, name="test"):
        merged = await _merge_llm_arbitrated(
            rule, text_ev, vision_ev, llm=None, ocr_text="x",
        )

    # Higher severity wins on fallback.
    assert merged.status == "non_compliant"

    data = json.loads((doc_dir / "telemetry-test.json").read_text())
    fb_events = [
        e for e in data.get("events", [])
        if e.get("event") == "compliance.arbitration_fallback_used"
    ]
    assert fb_events, (
        "compliance.arbitration_fallback_used did not fire for "
        "llm=None path"
    )
    fields = fb_events[0].get("fields", {})
    assert fields.get("reason") == "no_llm_provided"
    assert fields.get("rule_id") == "GMP-EQU11"
    assert fields.get("text_status") == "compliant"
    assert fields.get("vision_status") == "non_compliant"


@pytest.mark.asyncio
async def test_arbitration_fallback_telemetry_fires_when_arbitrator_raises(
    tmp_path,
) -> None:
    """When the arbitrator LLM call raises (timeout, parse error,
    rate-limit), the fallback fires with a ``reason`` that names
    the exception class — distinguishable from the no-llm case."""

    from app.compliance.evaluator import _merge_llm_arbitrated
    from app.compliance.models import RuleEvaluation
    from app.compliance.rules.registry import AuditRule
    from app.observability.run_telemetry import telemetry_run

    rule = AuditRule(
        id="GMP-EQU11",
        number=11,
        category="final_sign_off",
        category_display="Final Sign-off",
        agent="gmp",
        text="dummy",
        evaluation_strategy="llm_arbitrated",
    )
    text_ev = RuleEvaluation(rule_id="GMP-EQU11", status="compliant", confidence=0.9, reasoning="t")
    vision_ev = RuleEvaluation(rule_id="GMP-EQU11", status="non_compliant", confidence=0.85, reasoning="v")

    class BrokenLLM:
        async def generate(self, *a, **kw):
            raise RuntimeError("rate_limited")
    broken_llm = BrokenLLM()

    doc_id = "test-arb-raises"
    doc_dir = tmp_path / doc_id

    with telemetry_run(doc_id, doc_dir, name="test"):
        merged = await _merge_llm_arbitrated(
            rule, text_ev, vision_ev, llm=broken_llm, ocr_text="x",
        )

    assert merged.status == "non_compliant"  # fallback wins

    data = json.loads((doc_dir / "telemetry-test.json").read_text())
    fb_events = [
        e for e in data.get("events", [])
        if e.get("event") == "compliance.arbitration_fallback_used"
    ]
    assert fb_events, "fallback telemetry must fire when arbitrator raises"
    reason = fb_events[0].get("fields", {}).get("reason", "")
    assert "arbitration_raised" in reason and "RuntimeError" in reason, (
        f"reason must name the exception class so post-run analysis "
        f"can group failure modes; got {reason!r}"
    )
