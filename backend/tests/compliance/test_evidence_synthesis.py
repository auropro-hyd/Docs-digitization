"""Unit tests for evidence synthesis helpers."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock

from app.compliance.evaluator import _synthesize_batch
from app.compliance.models import RuleBatchResult, RuleEvaluation


def _make_llm(response: str) -> AsyncMock:
    llm = AsyncMock()
    llm.generate = AsyncMock(return_value=response)
    return llm


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
        assert "PAGE:3" in result["CHE-AUD1"]

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self):
        chunk = {"CHE-X1": [(1, "ev", "compliant"), (2, "ev2", "compliant"),
                             (3, "ev3", "compliant"), (4, "ev4", "compliant")]}
        fenced = "```json\n" + json.dumps({"CHE-X1": "Narrative PAGE:1."}) + "\n```"
        llm = _make_llm(fenced)

        result = await _synthesize_batch(chunk, llm)

        assert result["CHE-X1"] == "Narrative PAGE:1."

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
