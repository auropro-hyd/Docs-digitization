# backend/tests/compliance/test_merge_strategies.py
"""Unit tests for evaluation merge strategy helpers."""
import pytest
from app.compliance.evaluator import _merge_text_vision
from app.compliance.rules.registry import AuditRule
from app.compliance.models import RuleEvaluation


def _rule(strategy: str = "text_and_vision") -> AuditRule:
    return AuditRule(
        id="alcoa:5",
        number=5,
        category="attributable",
        category_display="Attributable",
        agent="alcoa",
        text="Test rule",
        evaluation_strategy=strategy,
    )


def _ev(rule_id: str, status: str, confidence: float = 0.9) -> RuleEvaluation:
    return RuleEvaluation(rule_id=rule_id, status=status, confidence=confidence)


# ── existing text_and_vision merge (regression) ───────────────────────────────

class TestMergeTextVision:
    def test_vision_wins_tie(self):
        rule = _rule("text_and_vision")
        text = _ev(rule.id, "compliant")
        vision = _ev(rule.id, "compliant")
        result = _merge_text_vision(rule, text, vision)
        assert result.status == "compliant"
        assert "[Vision]" in result.reasoning

    def test_vision_wins_when_higher_severity(self):
        rule = _rule("text_and_vision")
        text = _ev(rule.id, "compliant")
        vision = _ev(rule.id, "non_compliant")
        result = _merge_text_vision(rule, text, vision)
        assert result.status == "non_compliant"

    def test_text_wins_when_higher_severity(self):
        rule = _rule("text_and_vision")
        text = _ev(rule.id, "non_compliant")
        vision = _ev(rule.id, "compliant")
        result = _merge_text_vision(rule, text, vision)
        assert result.status == "non_compliant"

    def test_none_text_returns_vision(self):
        rule = _rule("text_and_vision")
        vision = _ev(rule.id, "compliant")
        result = _merge_text_vision(rule, None, vision)
        assert result.status == "compliant"

    def test_none_vision_returns_text(self):
        rule = _rule("text_and_vision")
        text = _ev(rule.id, "uncertain")
        result = _merge_text_vision(rule, text, None)
        assert result.status == "uncertain"

    def test_both_none_returns_error(self):
        rule = _rule("text_and_vision")
        result = _merge_text_vision(rule, None, None)
        assert result.status == "error"


# ── text_primary merge ────────────────────────────────────────────────────────

class TestMergeTextPrimary:
    def test_text_wins_tie(self):
        from app.compliance.evaluator import _merge_text_primary
        rule = _rule("text_primary")
        text = _ev(rule.id, "compliant")
        vision = _ev(rule.id, "compliant")
        result = _merge_text_primary(rule, text, vision)
        assert result.status == "compliant"
        assert "[Text]" in result.reasoning

    def test_vision_escalates_to_non_compliant(self):
        from app.compliance.evaluator import _merge_text_primary
        rule = _rule("text_primary")
        text = _ev(rule.id, "compliant")
        vision = _ev(rule.id, "non_compliant")
        result = _merge_text_primary(rule, text, vision)
        assert result.status == "non_compliant"
        assert "[Vision]" in result.reasoning

    def test_text_wins_over_lower_severity_vision(self):
        from app.compliance.evaluator import _merge_text_primary
        rule = _rule("text_primary")
        text = _ev(rule.id, "non_compliant")
        vision = _ev(rule.id, "compliant")
        result = _merge_text_primary(rule, text, vision)
        assert result.status == "non_compliant"
        assert "[Text]" in result.reasoning

    def test_text_wins_over_uncertain_vision(self):
        from app.compliance.evaluator import _merge_text_primary
        rule = _rule("text_primary")
        text = _ev(rule.id, "uncertain")
        vision = _ev(rule.id, "compliant")
        result = _merge_text_primary(rule, text, vision)
        assert result.status == "uncertain"

    def test_none_text_returns_vision(self):
        from app.compliance.evaluator import _merge_text_primary
        rule = _rule("text_primary")
        vision = _ev(rule.id, "compliant")
        result = _merge_text_primary(rule, None, vision)
        assert result.status == "compliant"

    def test_none_vision_returns_text(self):
        from app.compliance.evaluator import _merge_text_primary
        rule = _rule("text_primary")
        text = _ev(rule.id, "non_compliant")
        result = _merge_text_primary(rule, text, None)
        assert result.status == "non_compliant"

    def test_both_none_returns_error(self):
        from app.compliance.evaluator import _merge_text_primary
        rule = _rule("text_primary")
        result = _merge_text_primary(rule, None, None)
        assert result.status == "error"


# ── llm_arbitrated merge ──────────────────────────────────────────────────────

import asyncio
from unittest.mock import AsyncMock, patch


class TestMergeLLMArbitrated:
    def test_agreement_compliant_no_llm_call(self):
        from app.compliance.evaluator import _merge_llm_arbitrated
        rule = _rule("llm_arbitrated")
        text = _ev(rule.id, "compliant")
        vision = _ev(rule.id, "compliant")

        async def run():
            with patch("app.compliance.evaluator._call_arbitrator") as mock_arb:
                result = await _merge_llm_arbitrated(rule, text, vision, llm=None, ocr_text="")
                mock_arb.assert_not_called()
                return result

        result = asyncio.run(run())
        assert result.status == "compliant"

    def test_agreement_non_compliant_no_llm_call(self):
        from app.compliance.evaluator import _merge_llm_arbitrated
        rule = _rule("llm_arbitrated")
        text = _ev(rule.id, "non_compliant")
        vision = _ev(rule.id, "non_compliant")

        async def run():
            with patch("app.compliance.evaluator._call_arbitrator") as mock_arb:
                result = await _merge_llm_arbitrated(rule, text, vision, llm=None, ocr_text="")
                mock_arb.assert_not_called()
                return result

        result = asyncio.run(run())
        assert result.status == "non_compliant"

    def test_conflict_llm_called_and_verdict_used(self):
        from app.compliance.evaluator import _merge_llm_arbitrated
        rule = _rule("llm_arbitrated")
        text = _ev(rule.id, "non_compliant")
        vision = _ev(rule.id, "compliant")
        arbitrated = _ev(rule.id, "compliant", confidence=0.85)
        arbitrated.reasoning = "OCR missed handwritten signature; vision confirms presence"

        async def run():
            with patch("app.compliance.evaluator._call_arbitrator", new=AsyncMock(return_value=arbitrated)):
                result = await _merge_llm_arbitrated(rule, text, vision, llm=object(), ocr_text="Done by: [Signature]")
                return result

        result = asyncio.run(run())
        assert result.status == "compliant"
        assert "Arbitrated" in result.reasoning

    def test_conflict_arbitrator_fails_falls_back_to_higher_severity(self):
        from app.compliance.evaluator import _merge_llm_arbitrated
        rule = _rule("llm_arbitrated")
        text = _ev(rule.id, "non_compliant")
        vision = _ev(rule.id, "compliant")

        async def run():
            with patch("app.compliance.evaluator._call_arbitrator", new=AsyncMock(side_effect=Exception("timeout"))):
                result = await _merge_llm_arbitrated(rule, text, vision, llm=object(), ocr_text="")
                return result

        result = asyncio.run(run())
        assert result.status == "non_compliant"  # higher severity wins as fallback

    def test_none_text_returns_vision(self):
        from app.compliance.evaluator import _merge_llm_arbitrated
        rule = _rule("llm_arbitrated")
        vision = _ev(rule.id, "compliant")

        result = asyncio.run(_merge_llm_arbitrated(rule, None, vision, llm=None, ocr_text=""))
        assert result.status == "compliant"

    def test_none_vision_returns_text(self):
        from app.compliance.evaluator import _merge_llm_arbitrated
        rule = _rule("llm_arbitrated")
        text = _ev(rule.id, "uncertain")

        result = asyncio.run(_merge_llm_arbitrated(rule, text, None, llm=None, ocr_text=""))
        assert result.status == "uncertain"


# ── routing integration ───────────────────────────────────────────────────────

class TestRoutingStrategy:
    def test_text_primary_reasoning_prefix(self):
        from app.compliance.evaluator import _merge_text_primary
        rule = _rule("text_primary")
        text = _ev(rule.id, "non_compliant")
        vision = _ev(rule.id, "compliant")
        result = _merge_text_primary(rule, text, vision)
        assert "[Text]" in result.reasoning or "[Text primary]" in result.reasoning

    def test_llm_arbitrated_agreement_prefix(self):
        from app.compliance.evaluator import _merge_llm_arbitrated
        rule = _rule("llm_arbitrated")
        text = _ev(rule.id, "compliant")
        vision = _ev(rule.id, "compliant")
        result = asyncio.run(_merge_llm_arbitrated(rule, text, vision, llm=None, ocr_text=""))
        assert "[Agreed]" in result.reasoning
