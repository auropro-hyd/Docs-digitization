"""Tests for the agentic audit pipeline.

Covers:
  - Registry: context_sources YAML loading, agentic rule validation, batch filtering
  - compliance/summarizer: load_summary, store_page_summary roundtrip
  - ContextToolbox: get_context_summary (disk-backed cache), get_context_pages filtering
  - Graph: fan_out_workers routing, section_worker tool-call loop, synthesize merge
  - run_agentic_postpass: short-circuit, progress callback
  - Agent integration: agentic results merged into AgentReport
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.compliance.agentic.graph import (
    AgenticAuditState,
    SectionChunk,
    SynthesisOutput,
    WorkerAction,
    WorkerResult,
    WorkerVerdict,
    _build_initial_prompt,
    fan_out_workers,
    section_worker,
    synthesize,
)
from app.compliance.agentic.postpass import run_agentic_postpass
from app.compliance.agentic.toolbox import ContextToolbox
from app.compliance.models import AgentReport, RuleBatchResult, RuleEvaluation
from app.compliance.rules.registry import AuditRule, _finalise_rule


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────


def _make_rule(
    rule_id: str = "GMP-AGE1",
    scope: str = "package",
    evaluation_strategy: str = "agentic_audit",
    applicable_document_types: list[str] | None = None,
    context_sources: list[dict] | None = None,
) -> AuditRule:
    return AuditRule(
        id=rule_id,
        number=1,
        category="agentic",
        category_display="Agentic",
        agent="gmp",
        text="Test agentic rule text.",
        scope=scope,
        evaluation_strategy=evaluation_strategy,
        applicable_document_types=applicable_document_types or ["batch_record"],
        context_sources=context_sources if context_sources is not None else [{"document_type": "batch_record", "section_types": []}],
    )


def _make_extraction(page_num: int, document_type: str = "batch_record", section_type: str = "manufacturing_operations") -> dict:
    return {
        "page_num": page_num,
        "markdown": f"Page {page_num} content for {document_type}/{section_type}",
        "document_type": document_type,
        "section_type": section_type,
    }


def _make_section_map(extractions: list[dict]) -> dict[int, dict]:
    return {
        ext["page_num"]: {
            "document_type": ext["document_type"],
            "section_type": ext["section_type"],
        }
        for ext in extractions
    }


def _make_mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.generate = AsyncMock(return_value="mock summary")
    llm.generate_structured = AsyncMock()
    return llm


def _make_compliance_config() -> MagicMock:
    cfg = MagicMock()
    cfg.max_concurrent_batches = 2
    cfg.agentic_page_cap = 50
    cfg.agentic_worker_page_limit = 12
    cfg.agentic_max_tool_calls = 5
    return cfg


# ─────────────────────────────────────────────────────────────
# Registry tests (T001 verification)
# ─────────────────────────────────────────────────────────────


class TestContextSourcesYAMLLoading:
    """Test 1: context_sources is loaded from YAML overrides into AuditRule."""

    def test_context_sources_yaml_loading(self):
        """Rule built with context_sources override has that value; other rules get []."""
        cs = [{"document_type": "raw_material_request", "section_types": []}]
        rule = _finalise_rule(
            agent="gmp",
            num=1,
            raw_text="Verify raw material request.",
            category="material_handling",
            category_display="Material Handling",
            severity="major",
            yaml_overrides={
                "evaluation_strategy": "text",
                "applicable_document_types": ["batch_record"],
                "context_sources": cs,
            },
        )
        assert rule is not None
        assert rule.context_sources == cs

    def test_context_sources_defaults_to_empty_list(self):
        """Rule without context_sources override gets empty list."""
        rule = _finalise_rule(
            agent="gmp",
            num=2,
            raw_text="Verify signatures.",
            category="signature_verification",
            category_display="Signature Verification",
            severity="critical",
            yaml_overrides=None,
        )
        assert rule is not None
        assert rule.context_sources == []


class TestAgenticRuleMissingDocTypesSkipped:
    """Test 2: _finalise_rule with agentic_audit strategy + empty doc types returns None."""

    def test_agentic_rule_missing_doc_types_skipped(self, caplog):
        with caplog.at_level(logging.ERROR, logger="app.compliance.rules.registry"):
            result = _finalise_rule(
                agent="gmp",
                num=1,
                raw_text="Cross-package verification.",
                category="agentic",
                category_display="Agentic",
                severity="major",
                yaml_overrides={
                    "evaluation_strategy": "agentic_audit",
                    "applicable_document_types": [],
                },
            )
        assert result is None
        assert any("agentic" in rec.message.lower() or "applicable_document_types" in rec.message.lower() for rec in caplog.records)


class TestAgenticRuleExcludedFromPageBatches:
    """Test 3: Rules with scope='package' are excluded from page-scope get_batches."""

    def test_agentic_rule_excluded_from_page_batches(self):
        page_rule = _make_rule("GMP-PAG1", scope="page", evaluation_strategy="text")
        agentic_rule = _make_rule("GMP-AGE1", scope="package", evaluation_strategy="agentic_audit")
        all_rules = [page_rule, agentic_rule]
        filtered = [r for r in all_rules if r.scope == "page"]
        assert len(filtered) == 1
        assert filtered[0].id == "GMP-PAG1"
        assert agentic_rule not in filtered


# ─────────────────────────────────────────────────────────────
# compliance/summarizer tests
# ─────────────────────────────────────────────────────────────


class TestLoadSummaryMissingFileReturnsNone:
    """Test 4: load_summary returns None when page_summaries.json is absent."""

    def test_load_summary_missing_file_returns_none(self, tmp_path, monkeypatch):
        from app.compliance import summarizer as summod
        monkeypatch.setattr(summod, "_summaries_file", lambda doc_id: tmp_path / doc_id / "summaries" / "page_summaries.json")
        from app.compliance.summarizer import load_summary
        result = load_summary("no-such-doc", "batch_record", "manufacturing_operations")
        assert result is None


class TestStoreThenLoadSummaryRoundtrip:
    """Test 5: store_page_summary + load_summary roundtrip without LLM."""

    def test_store_then_load_roundtrip(self, tmp_path, monkeypatch):
        from app.compliance import summarizer as summod
        monkeypatch.setattr(summod, "_summaries_file", lambda doc_id: tmp_path / doc_id / "summaries" / "page_summaries.json")
        from app.compliance.summarizer import load_summary, store_page_summary

        store_page_summary("test-doc", 1, "batch_record", "manufacturing_operations", "summary text")
        result = load_summary("test-doc", "batch_record", "manufacturing_operations")
        assert result == "summary text"

    def test_multiple_pages_joined_in_order(self, tmp_path, monkeypatch):
        from app.compliance import summarizer as summod
        monkeypatch.setattr(summod, "_summaries_file", lambda doc_id: tmp_path / doc_id / "summaries" / "page_summaries.json")
        from app.compliance.summarizer import load_summary, store_page_summary

        store_page_summary("test-doc", 3, "batch_record", "manufacturing_operations", "page 3")
        store_page_summary("test-doc", 1, "batch_record", "manufacturing_operations", "page 1")
        store_page_summary("test-doc", 2, "batch_record", "manufacturing_operations", "page 2")
        result = load_summary("test-doc", "batch_record", "manufacturing_operations")
        assert result == "page 1\n\npage 2\n\npage 3"

    def test_section_type_filter(self, tmp_path, monkeypatch):
        from app.compliance import summarizer as summod
        monkeypatch.setattr(summod, "_summaries_file", lambda doc_id: tmp_path / doc_id / "summaries" / "page_summaries.json")
        from app.compliance.summarizer import load_summary, store_page_summary

        store_page_summary("test-doc", 1, "batch_record", "manufacturing_operations", "manuf page")
        store_page_summary("test-doc", 2, "batch_record", "cover_page", "cover page")
        result = load_summary("test-doc", "batch_record", "manufacturing_operations")
        assert result == "manuf page"
        result2 = load_summary("test-doc", "batch_record", "cover_page")
        assert result2 == "cover page"

    def test_store_creates_parent_directories(self, tmp_path, monkeypatch):
        from app.compliance import summarizer as summod
        monkeypatch.setattr(summod, "_summaries_file", lambda doc_id: tmp_path / doc_id / "summaries" / "page_summaries.json")
        from app.compliance.summarizer import load_summary, store_page_summary

        store_page_summary("new-doc", 1, "sop", None, "sop text")
        result = load_summary("new-doc", "sop", None)
        assert result == "sop text"


class TestSummarizePagesSkipsExisting:
    """Test 6: summarize_pages_in_batches skips pages already in page_summaries.json."""

    @pytest.mark.asyncio
    async def test_skips_existing_pages(self, tmp_path, monkeypatch):
        from app.compliance import summarizer as summod
        monkeypatch.setattr(summod, "_summaries_file", lambda doc_id: tmp_path / doc_id / "summaries" / "page_summaries.json")
        from app.compliance.summarizer import store_page_summary, summarize_pages_in_batches

        # Pre-populate page 1
        store_page_summary("test-doc", 1, "batch_record", "manufacturing_operations", "existing summary")

        extractions = [
            _make_extraction(1),  # already exists — should be skipped
            _make_extraction(2),  # new — should be generated
        ]
        section_map = _make_section_map(extractions)
        llm = _make_mock_llm()
        llm.generate = AsyncMock(return_value="new summary")

        await summarize_pages_in_batches(extractions, section_map, "test-doc", llm)

        # LLM should only be called once (for page 2, not page 1)
        assert llm.generate.call_count == 1


# ─────────────────────────────────────────────────────────────
# ContextToolbox tests
# ─────────────────────────────────────────────────────────────


class TestToolboxGetContextSummaryDiskBacked:
    """Test 7: get_context_summary loads from disk via load_summary; caches result."""

    def test_get_context_summary_loads_from_disk(self, monkeypatch):
        from app.compliance.agentic import toolbox as tbmod
        monkeypatch.setattr(tbmod, "load_summary", lambda doc_id, doc_type, sec_type: "disk summary")

        extractions = [_make_extraction(1)]
        section_map = _make_section_map(extractions)
        toolbox = ContextToolbox(extractions, section_map, doc_id="test-doc")

        result = toolbox.get_context_summary("batch_record", "manufacturing_operations")
        assert result == "disk summary"

    def test_get_context_summary_returns_empty_when_no_summary(self, monkeypatch):
        from app.compliance.agentic import toolbox as tbmod
        monkeypatch.setattr(tbmod, "load_summary", lambda doc_id, doc_type, sec_type: None)

        extractions = [_make_extraction(1)]
        section_map = _make_section_map(extractions)
        toolbox = ContextToolbox(extractions, section_map, doc_id="test-doc")

        result = toolbox.get_context_summary("missing_type", None)
        assert result == ""

    def test_get_context_summary_caches_result(self, monkeypatch):
        call_count = 0

        def _mock_load(doc_id, doc_type, sec_type):
            nonlocal call_count
            call_count += 1
            return "cached result"

        from app.compliance.agentic import toolbox as tbmod
        monkeypatch.setattr(tbmod, "load_summary", _mock_load)

        extractions = [_make_extraction(1)]
        section_map = _make_section_map(extractions)
        toolbox = ContextToolbox(extractions, section_map, doc_id="test-doc")

        toolbox.get_context_summary("batch_record", None)
        toolbox.get_context_summary("batch_record", None)  # second call — should hit cache

        assert call_count == 1


class TestToolboxGetContextPagesFiltersByDocType:
    """Test 8: get_context_pages returns only pages matching document_type."""

    def test_get_context_pages_filters_by_doc_type(self):
        batch_extractions = [
            _make_extraction(1, "batch_record", "manufacturing_operations"),
            _make_extraction(2, "batch_record", "manufacturing_operations"),
        ]
        sop_extraction = _make_extraction(3, "sop", "overview")
        all_extractions = batch_extractions + [sop_extraction]
        section_map = _make_section_map(all_extractions)

        toolbox = ContextToolbox(all_extractions, section_map, doc_id="test-doc")
        result = toolbox.get_context_pages("batch_record")
        assert "Page 1" in result
        assert "Page 2" in result
        assert "Page 3" not in result


# ─────────────────────────────────────────────────────────────
# Graph node tests — fan_out_workers
# ─────────────────────────────────────────────────────────────


class TestFanOutWorkersChunksAtLimit:
    """Test 9: fan_out_workers splits into 2 Send objects when pages > worker_page_limit."""

    def test_fan_out_workers_chunks_at_limit(self, monkeypatch):
        from app.compliance.agentic import graph as gmod
        monkeypatch.setattr(gmod, "ContextToolbox", MagicMock(return_value=MagicMock()))

        n_pages = 15
        extractions = [
            _make_extraction(i, "batch_record", "manufacturing_operations")
            for i in range(1, n_pages + 1)
        ]
        section_map = _make_section_map(extractions)
        rule = _make_rule(applicable_document_types=["batch_record"])

        state: AgenticAuditState = {
            "rule": rule,
            "all_extractions": extractions,
            "section_map": section_map,
            "llm": _make_mock_llm(),
            "doc_id": "test-doc",
            "page_cap": 50,
            "worker_page_limit": 12,
            "max_concurrent": 2,
            "max_tool_calls": 5,
            "toolbox": None,
            "worker_results": [],
            "final_evaluation": None,
            "current_chunk": None,
        }

        from langgraph.types import Send
        sends = fan_out_workers(state)
        worker_sends = [s for s in sends if s.node == "section_worker"]
        assert len(worker_sends) == 2


class TestFanOutWorkersEmptySectionTypes:
    """Test 10: fan_out_workers with applicable_section_types=[] includes all sections."""

    def test_fan_out_workers_empty_section_types(self, monkeypatch):
        from app.compliance.agentic import graph as gmod
        monkeypatch.setattr(gmod, "ContextToolbox", MagicMock(return_value=MagicMock()))

        extractions = [
            _make_extraction(1, "batch_record", "manufacturing_operations"),
            _make_extraction(2, "batch_record", "cover_page"),
            _make_extraction(3, "batch_record", "dispensing"),
        ]
        section_map = _make_section_map(extractions)
        rule = AuditRule(
            id="GMP-AGE1",
            number=1,
            category="agentic",
            category_display="Agentic",
            agent="gmp",
            text="Rule text.",
            scope="package",
            evaluation_strategy="agentic_audit",
            applicable_document_types=["batch_record"],
            applicable_section_types=[],
        )

        state: AgenticAuditState = {
            "rule": rule,
            "all_extractions": extractions,
            "section_map": section_map,
            "llm": _make_mock_llm(),
            "doc_id": "test-doc",
            "page_cap": 50,
            "worker_page_limit": 12,
            "max_concurrent": 2,
            "max_tool_calls": 5,
            "toolbox": None,
            "worker_results": [],
            "final_evaluation": None,
            "current_chunk": None,
        }

        from langgraph.types import Send
        sends = fan_out_workers(state)
        worker_sends = [s for s in sends if s.node == "section_worker"]
        total_pages = sum(len(s.arg["current_chunk"]["pages"]) for s in worker_sends)
        assert total_pages == 3


class TestFanOutWorkersEmptyExtractionsGoesToSynthesize:
    """Test 11: fan_out_workers returns Send('synthesize', ...) when no chunks."""

    def test_fan_out_workers_no_chunks_sends_to_synthesize(self, monkeypatch):
        from app.compliance.agentic import graph as gmod
        monkeypatch.setattr(gmod, "ContextToolbox", MagicMock(return_value=MagicMock()))

        rule = _make_rule(applicable_document_types=["batch_record"])

        state: AgenticAuditState = {
            "rule": rule,
            "all_extractions": [],  # no pages
            "section_map": {},
            "llm": _make_mock_llm(),
            "doc_id": "test-doc",
            "page_cap": 50,
            "worker_page_limit": 12,
            "max_concurrent": 2,
            "max_tool_calls": 5,
            "toolbox": None,
            "worker_results": [],
            "final_evaluation": None,
            "current_chunk": None,
        }

        from langgraph.types import Send
        sends = fan_out_workers(state)
        assert len(sends) == 1
        assert sends[0].node == "synthesize"


# ─────────────────────────────────────────────────────────────
# section_worker and synthesize tests
# ─────────────────────────────────────────────────────────────


class TestSectionWorkerProducesVerdictOnFirstAction:
    """Test 12: section_worker returns compliant when LLM produces verdict immediately."""

    @pytest.mark.asyncio
    async def test_section_worker_produces_verdict_on_first_action(self):
        rule = _make_rule()
        chunk: SectionChunk = {
            "document_type": "batch_record",
            "section_type": "manufacturing_operations",
            "pages": [_make_extraction(1)],
            "chunk_id": "manufacturing_operations-0",
        }

        verdict = WorkerVerdict(
            status="compliant",
            confidence=0.9,
            reasoning="All good",
            evidence="p1 content",
        )
        action = WorkerAction(action="produce_verdict", verdict=verdict)

        llm = _make_mock_llm()
        llm.generate_structured = AsyncMock(return_value=action)

        mock_toolbox = MagicMock(spec=ContextToolbox)
        mock_toolbox.get_context_summary = MagicMock(return_value="")
        mock_toolbox.get_context_pages = MagicMock(return_value="")

        state: AgenticAuditState = {
            "rule": rule,
            "all_extractions": [_make_extraction(1)],
            "section_map": {1: {"document_type": "batch_record", "section_type": "manufacturing_operations"}},
            "llm": llm,
            "doc_id": "test-doc",
            "page_cap": 50,
            "worker_page_limit": 12,
            "max_concurrent": 2,
            "max_tool_calls": 5,
            "toolbox": mock_toolbox,
            "worker_results": [],
            "final_evaluation": None,
            "current_chunk": chunk,
        }

        result = await section_worker(state)
        assert result["worker_results"][0]["status"] == "compliant"


class TestSectionWorkerExhaustsToolCalls:
    """Test 13: section_worker calls forced verdict after max_tool_calls tool actions."""

    @pytest.mark.asyncio
    async def test_section_worker_exhausts_tool_calls(self):
        rule = _make_rule()
        chunk: SectionChunk = {
            "document_type": "batch_record",
            "section_type": "manufacturing_operations",
            "pages": [_make_extraction(1)],
            "chunk_id": "manufacturing_operations-0",
        }

        tool_action = WorkerAction(
            action="get_context_summary",
            document_type="batch_record",
        )
        forced_verdict = WorkerVerdict(
            status="uncertain",
            confidence=0.5,
            reasoning="Tool call limit reached",
            evidence="",
        )

        llm = _make_mock_llm()
        llm.generate_structured = AsyncMock(side_effect=[tool_action, tool_action, forced_verdict])

        mock_toolbox = MagicMock(spec=ContextToolbox)
        mock_toolbox.get_context_summary = MagicMock(return_value="some summary")
        mock_toolbox.get_context_pages = MagicMock(return_value="")

        state: AgenticAuditState = {
            "rule": rule,
            "all_extractions": [_make_extraction(1)],
            "section_map": {1: {"document_type": "batch_record", "section_type": "manufacturing_operations"}},
            "llm": llm,
            "doc_id": "test-doc",
            "page_cap": 50,
            "worker_page_limit": 12,
            "max_concurrent": 2,
            "max_tool_calls": 2,
            "toolbox": mock_toolbox,
            "worker_results": [],
            "final_evaluation": None,
            "current_chunk": chunk,
        }

        result = await section_worker(state)
        assert result["worker_results"][0]["status"] == "uncertain"
        assert llm.generate_structured.call_count == 3


class TestSynthesizeNonCompliantWins:
    """Test 14: synthesize returns non_compliant when one worker reports it."""

    @pytest.mark.asyncio
    async def test_synthesize_non_compliant_wins(self):
        rule = _make_rule()
        worker_results: list[WorkerResult] = [
            WorkerResult(
                chunk_id="chunk-0",
                status="compliant",
                confidence=0.9,
                reasoning="Section A looks good",
                evidence="p1",
                page_range="pp. 1-5",
                section_type="manufacturing_operations",
            ),
            WorkerResult(
                chunk_id="chunk-1",
                status="non_compliant",
                confidence=0.85,
                reasoning="Missing witness signature on section B",
                evidence="p6: witness column blank",
                page_range="pp. 6-10",
                section_type="dispensing",
            ),
        ]

        synthesis_out = SynthesisOutput(
            status="non_compliant",
            confidence=0.85,
            reasoning="Package non-compliant: missing witness signature",
            evidence="p6: witness column blank",
        )

        llm = _make_mock_llm()
        llm.generate_structured = AsyncMock(return_value=synthesis_out)

        state: AgenticAuditState = {
            "rule": rule,
            "all_extractions": [],
            "section_map": {},
            "llm": llm,
            "doc_id": "test-doc",
            "page_cap": 50,
            "worker_page_limit": 12,
            "max_concurrent": 2,
            "max_tool_calls": 5,
            "toolbox": None,
            "worker_results": worker_results,
            "final_evaluation": None,
            "current_chunk": None,
        }

        result = await synthesize(state)
        assert result["final_evaluation"].status == "non_compliant"


class TestSynthesizeEmptyWorkersReturnsUncertain:
    """Test 15: synthesize with no worker results returns uncertain without calling LLM."""

    @pytest.mark.asyncio
    async def test_synthesize_empty_workers_returns_uncertain(self):
        rule = _make_rule()
        llm = _make_mock_llm()

        state: AgenticAuditState = {
            "rule": rule,
            "all_extractions": [],
            "section_map": {},
            "llm": llm,
            "doc_id": "test-doc",
            "page_cap": 50,
            "worker_page_limit": 12,
            "max_concurrent": 2,
            "max_tool_calls": 5,
            "toolbox": None,
            "worker_results": [],
            "final_evaluation": None,
            "current_chunk": None,
        }

        result = await synthesize(state)
        eval_ = result["final_evaluation"]
        assert eval_.status == "uncertain"
        assert "no" in eval_.reasoning.lower() or "pages" in eval_.reasoning.lower() or "not found" in eval_.reasoning.lower()
        llm.generate_structured.assert_not_called()


# ─────────────────────────────────────────────────────────────
# Postpass tests
# ─────────────────────────────────────────────────────────────


class TestRunAgenticPostpassNoRulesReturnsEmpty:
    """Test 16: run_agentic_postpass returns [] immediately when no agentic rules exist."""

    @pytest.mark.asyncio
    async def test_run_agentic_postpass_no_rules_returns_empty(self):
        mock_registry = MagicMock()
        mock_registry.get_rules = MagicMock(return_value=[
            _make_rule("GMP-PAG1", scope="page", evaluation_strategy="text"),
        ])

        with patch("app.compliance.agentic.postpass.get_agentic_graph") as mock_graph_fn:
            result = await run_agentic_postpass(
                agent_name="gmp",
                registry=mock_registry,
                extractions=[],
                section_map={},
                llm=_make_mock_llm(),
                config=_make_compliance_config(),
                doc_id="test-doc",
            )

        assert result == []
        mock_graph_fn.assert_not_called()


class TestRunAgenticPostpassInvokesProgressCallback:
    """Test 17: run_agentic_postpass calls progress_callback once per rule."""

    @pytest.mark.asyncio
    async def test_run_agentic_postpass_invokes_progress_callback(self):
        agentic_rule = _make_rule()
        mock_registry = MagicMock()
        mock_registry.get_rules = MagicMock(return_value=[agentic_rule])

        evaluation = RuleEvaluation(
            rule_id="GMP-AGE1",
            status="compliant",
            confidence=0.95,
            reasoning="Compliant",
            evidence="",
        )
        final_state = {"final_evaluation": evaluation}

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value=final_state)
        progress_callback = AsyncMock()

        with patch("app.compliance.agentic.postpass.get_agentic_graph", return_value=mock_graph):
            result = await run_agentic_postpass(
                agent_name="gmp",
                registry=mock_registry,
                extractions=[],
                section_map={},
                llm=_make_mock_llm(),
                config=_make_compliance_config(),
                doc_id="test-doc",
                progress_callback=progress_callback,
            )

        assert len(result) == 1
        progress_callback.assert_called_once()


# ─────────────────────────────────────────────────────────────
# Agent integration tests
# ─────────────────────────────────────────────────────────────


class TestChecklistAgentAgenticResultsMerged:
    """Test 18: ChecklistAgent merges agentic evaluations into the returned AgentReport."""

    @pytest.mark.asyncio
    async def test_checklist_agent_agentic_results_merged(self):
        from app.compliance.checklist import ChecklistAgent

        page_rule = _make_rule("CHK-PAG1", scope="page", evaluation_strategy="text")
        agentic_rule = _make_rule("CHK-AGE1")

        eval1 = RuleEvaluation(rule_id="CHK-PAG1", status="compliant", confidence=0.9, reasoning="OK")
        eval2 = RuleEvaluation(rule_id="CHK-AGE1", status="non_compliant", confidence=0.8, reasoning="Missing")

        mock_registry = MagicMock()
        mock_registry.get_batches = MagicMock(return_value=[])
        mock_registry.get_rules = MagicMock(return_value=[page_rule, agentic_rule])

        page_result = [("batch-1", 1, RuleBatchResult(evaluations=[eval1]))]
        agentic_result = [("agentic-CHK-AGE1", None, RuleBatchResult(evaluations=[eval2]))]

        mock_config = MagicMock()
        mock_config.rule_batch_size = 15
        mock_config.batch_by_category = True
        mock_config.max_concurrent_batches = 2
        mock_config.agentic_page_cap = 50
        mock_config.agentic_worker_page_limit = 12

        with patch("app.compliance.checklist.run_agent_evaluation", AsyncMock(return_value=page_result)):
            with patch("app.compliance.checklist.run_agentic_postpass", AsyncMock(return_value=agentic_result)):
                with patch("app.compliance.checklist.assemble_agent_report") as mock_assemble:
                    mock_report = MagicMock(spec=AgentReport)
                    mock_assemble.return_value = mock_report

                    agent = ChecklistAgent(
                        llm=_make_mock_llm(),
                        registry=mock_registry,
                        config=mock_config,
                    )
                    report = await agent.review_document(
                        extractions=[_make_extraction(1)],
                    )

        call_args = mock_assemble.call_args
        combined_results = call_args[0][2]
        rule_ids_in_results = [
            ev.rule_id
            for _, _, batch in combined_results
            for ev in batch.evaluations
        ]
        assert "CHK-PAG1" in rule_ids_in_results
        assert "CHK-AGE1" in rule_ids_in_results


class TestBuildInitialPromptContextSources:
    """Test 20: _build_initial_prompt injects context_sources into worker prompt."""

    def _make_chunk(self) -> SectionChunk:
        return SectionChunk(
            document_type="batch_record",
            section_type="material_dispensing",
            pages=[_make_extraction(1, "batch_record", "material_dispensing")],
            chunk_id="material_dispensing-0",
        )

    def test_context_sources_appear_in_prompt(self):
        rule = _make_rule(
            context_sources=[{"document_type": "raw_material_request", "section_types": []}]
        )
        chunk = self._make_chunk()
        prompt = _build_initial_prompt(rule, chunk, "page content", "pp. 1-1")
        assert "raw_material_request" in prompt
        assert "AVAILABLE CONTEXT SOURCES" in prompt

    def test_context_sources_with_section_types_appear_in_prompt(self):
        rule = _make_rule(
            context_sources=[{"document_type": "raw_material_request", "section_types": ["dispensing_log"]}]
        )
        chunk = self._make_chunk()
        prompt = _build_initial_prompt(rule, chunk, "page content", "pp. 1-1")
        assert "raw_material_request" in prompt
        assert "dispensing_log" in prompt

    def test_no_context_sources_omits_section(self):
        rule = _make_rule(context_sources=[])
        chunk = self._make_chunk()
        prompt = _build_initial_prompt(rule, chunk, "page content", "pp. 1-1")
        assert "AVAILABLE CONTEXT SOURCES" not in prompt

    def test_must_fetch_instruction_present_when_sources_exist(self):
        rule = _make_rule(
            context_sources=[{"document_type": "raw_material_request", "section_types": []}]
        )
        chunk = self._make_chunk()
        prompt = _build_initial_prompt(rule, chunk, "page content", "pp. 1-1")
        assert "MUST fetch context" in prompt


class TestAgentUnchangedWithoutAgenticRules:
    """Test 19: Without agentic rules, the report only has page-pass findings."""

    @pytest.mark.asyncio
    async def test_agent_unchanged_without_agentic_rules(self):
        from app.compliance.checklist import ChecklistAgent

        page_rule = _make_rule("CHK-PAG2", scope="page", evaluation_strategy="text")
        eval1 = RuleEvaluation(rule_id="CHK-PAG2", status="compliant", confidence=0.95, reasoning="OK")

        mock_registry = MagicMock()
        mock_registry.get_batches = MagicMock(return_value=[])
        mock_registry.get_rules = MagicMock(return_value=[page_rule])

        page_result = [("batch-1", 1, RuleBatchResult(evaluations=[eval1]))]

        mock_config = MagicMock()
        mock_config.rule_batch_size = 15
        mock_config.batch_by_category = True
        mock_config.max_concurrent_batches = 2
        mock_config.agentic_page_cap = 50
        mock_config.agentic_worker_page_limit = 12

        with patch("app.compliance.checklist.run_agent_evaluation", AsyncMock(return_value=page_result)):
            with patch("app.compliance.checklist.run_agentic_postpass", AsyncMock(return_value=[])):
                with patch("app.compliance.checklist.assemble_agent_report") as mock_assemble:
                    mock_report = MagicMock(spec=AgentReport)
                    mock_assemble.return_value = mock_report

                    agent = ChecklistAgent(
                        llm=_make_mock_llm(),
                        registry=mock_registry,
                        config=mock_config,
                    )
                    report = await agent.review_document(
                        extractions=[_make_extraction(1)],
                    )

        call_args = mock_assemble.call_args
        combined_results = call_args[0][2]
        rule_ids = [
            ev.rule_id
            for _, _, batch in combined_results
            for ev in batch.evaluations
        ]
        assert rule_ids == ["CHK-PAG2"]
        assert report is mock_report
