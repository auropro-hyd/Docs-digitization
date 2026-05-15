"""Unit tests for evidence synthesis helpers."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.compliance.evaluator import _synthesize_batch, synthesize_rule_evidence
from app.compliance.models import RuleBatchResult, RuleEvaluation


def _make_llm(response: str) -> AsyncMock:
    llm = AsyncMock()
    llm.generate = AsyncMock(return_value=response)
    return llm


def _make_results(
    rule_id: str,
    page_evidences: list[tuple[int, str, str]],
) -> list[tuple[str, int, RuleBatchResult]]:
    """Build a minimal results list with one RuleEvaluation per page."""
    results = []
    for page_num, evidence, status in page_evidences:
        ev = RuleEvaluation(rule_id=rule_id, status=status, evidence=evidence)
        batch = RuleBatchResult(evaluations=[ev])
        results.append((f"batch-{page_num}", page_num, batch))
    return results


class TestSynthesizeBatch:
    @pytest.mark.asyncio
    async def test_returns_narrative_for_each_rule(self):
        chunk = {
            "CHE-AUD1": [(3, "Done by: S. Patel", "compliant"),
                         (5, "Checked by: R. Kumar", "compliant"),
                         (36, "Batch 400.000 Kg", "compliant"),
                         (41, "Seed Material issued", "compliant")],
        }
        payload = json.dumps({"CHE-AUD1": "Narrative citing PAGE:3 and PAGE:36."})
        llm = _make_llm(payload)

        result = await _synthesize_batch(chunk, llm)

        assert "CHE-AUD1" in result
        assert "PAGE:3" in result["CHE-AUD1"]["evidence"]

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self):
        chunk = {"CHE-X1": [(1, "ev", "compliant"), (2, "ev2", "compliant"),
                             (3, "ev3", "compliant"), (4, "ev4", "compliant")]}
        fenced = "```json\n" + json.dumps({"CHE-X1": "Narrative PAGE:1."}) + "\n```"
        llm = _make_llm(fenced)

        result = await _synthesize_batch(chunk, llm)

        assert result["CHE-X1"]["evidence"] == "Narrative PAGE:1."

    @pytest.mark.asyncio
    async def test_ignores_keys_not_in_chunk(self):
        chunk = {"CHE-A": [(1, "ev", "compliant"), (2, "ev2", "compliant"),
                            (3, "ev3", "compliant"), (4, "ev4", "compliant")]}
        payload = json.dumps({"CHE-A": "narrative", "CHE-Z": "should be dropped"})
        llm = _make_llm(payload)

        result = await _synthesize_batch(chunk, llm)

        assert "CHE-Z" not in result
        assert "CHE-A" in result

    @pytest.mark.asyncio
    async def test_raises_on_invalid_json(self):
        chunk = {"CHE-A": [(1, "ev", "compliant"), (2, "ev2", "compliant"),
                            (3, "ev3", "compliant"), (4, "ev4", "compliant")]}
        llm = _make_llm("not-json")

        with pytest.raises(Exception):
            await _synthesize_batch(chunk, llm)

    @pytest.mark.asyncio
    async def test_returns_evidence_and_reasoning_dict(self):
        """_synthesize_batch must return {rule_id: {evidence: ..., reasoning: ...}}."""
        chunk = {
            "CHE-DOC8": [
                (3, "Batch No entries have trailing dash 'C4060193-'", "non_compliant"),
                (4, "Same pattern for Seed Material sub-rows", "non_compliant"),
                (36, "Full lot number 'C4060193-02A' recorded here", "non_compliant"),
                (41, "Complete lot numbers for all other materials", "non_compliant"),
            ],
        }
        payload = json.dumps({
            "CHE-DOC8": {
                "evidence": "PAGE:3 shows trailing-dash batch entry 'C4060193-'. PAGE:36 records the full lot 'C4060193-02A'.",
                "reasoning": "Batch numbers on PAGE:3 and PAGE:4 are truncated; PAGE:36 confirms the complete value exists, making the BPCR entries non_compliant.",
            }
        })
        llm = _make_llm(payload)

        result = await _synthesize_batch(chunk, llm)

        assert "CHE-DOC8" in result
        assert isinstance(result["CHE-DOC8"], dict)
        assert "evidence" in result["CHE-DOC8"]
        assert "reasoning" in result["CHE-DOC8"]
        assert "PAGE:3" in result["CHE-DOC8"]["evidence"]
        assert "non_compliant" in result["CHE-DOC8"]["reasoning"].lower() or "truncated" in result["CHE-DOC8"]["reasoning"].lower()

    @pytest.mark.asyncio
    async def test_handles_plain_string_response_as_evidence_fallback(self):
        """If LLM returns a plain string (not nested dict), use it as evidence with empty reasoning."""
        chunk = {
            "CHE-X1": [(1, "ev", "compliant"), (2, "ev2", "compliant"),
                       (3, "ev3", "compliant"), (4, "ev4", "compliant")],
        }
        payload = json.dumps({"CHE-X1": "Plain narrative PAGE:1."})
        llm = _make_llm(payload)

        result = await _synthesize_batch(chunk, llm)

        assert result["CHE-X1"]["evidence"] == "Plain narrative PAGE:1."
        assert result["CHE-X1"]["reasoning"] == ""


class TestSynthesizeRuleEvidence:
    @pytest.mark.asyncio
    async def test_skips_rules_with_threshold_or_fewer_pages(self):
        """A rule with exactly 3 pages must NOT be synthesised."""
        results = _make_results(
            "CHE-A",
            [(1, "ev1", "compliant"), (2, "ev2", "compliant"), (3, "ev3", "compliant")],
        )
        llm = AsyncMock()
        llm.generate = AsyncMock()

        await synthesize_rule_evidence(results, llm, threshold=3)

        llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_patches_evidence_for_qualifying_rules(self):
        """A rule with 4 pages must have all its evidence fields overwritten."""
        page_entries = [
            (1, "ev1", "compliant"), (2, "ev2", "compliant"),
            (3, "ev3", "compliant"), (4, "ev4", "compliant"),
        ]
        results = _make_results("CHE-B", page_entries)
        narrative = "Synthesised narrative citing PAGE:1 and PAGE:4."
        llm = _make_llm(json.dumps({"CHE-B": narrative}))

        await synthesize_rule_evidence(results, llm, threshold=3)

        for _, _, batch in results:
            for ev in batch.evaluations:
                if ev.rule_id == "CHE-B":
                    assert ev.evidence == narrative

    @pytest.mark.asyncio
    async def test_skips_not_applicable_pages_for_threshold(self):
        """Pages with status=not_applicable must not count toward the threshold."""
        page_entries = [
            (1, "ev1", "compliant"), (2, "ev2", "compliant"),
            (3, "", "not_applicable"), (4, "ev4", "compliant"),
        ]
        results = _make_results("CHE-C", page_entries)
        llm = AsyncMock()
        llm.generate = AsyncMock()

        # 3 applicable pages (pages 1, 2, 4) — at threshold, so skip
        await synthesize_rule_evidence(results, llm, threshold=3)

        llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_failure_leaves_evidence_intact(self):
        """If the LLM raises, original evidence must be preserved."""
        page_entries = [
            (1, "original1", "compliant"), (2, "original2", "compliant"),
            (3, "original3", "compliant"), (4, "original4", "compliant"),
        ]
        results = _make_results("CHE-D", page_entries)
        llm = AsyncMock()
        llm.generate = AsyncMock(side_effect=RuntimeError("LLM down"))

        await synthesize_rule_evidence(results, llm, threshold=3)

        evidences = [
            ev.evidence
            for _, _, batch in results
            for ev in batch.evaluations
        ]
        assert all(e.startswith("original") for e in evidences)

    @pytest.mark.asyncio
    async def test_skips_none_page_num(self):
        """Document-scope results (page_num=None) must not be counted."""
        ev = RuleEvaluation(rule_id="CHE-E", status="compliant", evidence="doc-ev")
        batch = RuleBatchResult(evaluations=[ev])
        results = [("doc-batch", None, batch)]
        llm = AsyncMock()
        llm.generate = AsyncMock()

        await synthesize_rule_evidence(results, llm, threshold=3)

        llm.generate.assert_not_called()


class TestEvidenceFieldShape:
    """Schema regression: evidence field must remain a plain string."""

    def test_evidence_is_string_in_fixture(self):
        fixture = Path("data/documents/b2921434-25f4-4b7c-8509-233a72a3dd0c/compliance_result.json")
        if not fixture.exists():
            pytest.skip("Fixture file not present")
        data = json.loads(fixture.read_text())
        for finding in data.get("findings", []):
            assert isinstance(finding.get("evidence", ""), str), (
                f"evidence field for {finding['rule_id']} is not a string"
            )
        for ar in data.get("agent_reports", []):
            for ev in ar.get("all_evaluations", []):
                assert isinstance(ev.get("evidence", ""), str)
