"""LangGraph flow for agentic audit evaluation."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Annotated, Literal, TypedDict

import operator

_AGENTIC_DEBUG = os.getenv("COMPLIANCE_AGENTIC_DEBUG") == "1"


def _trace(rule_id: str, step: dict) -> None:
    """Append one step to /tmp/agentic_trace_<rule_id>.jsonl when debug is on."""
    if not _AGENTIC_DEBUG:
        return
    trace_path = Path(tempfile.gettempdir()) / f"agentic_trace_{rule_id}.jsonl"
    with trace_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(step, ensure_ascii=False) + "\n")
    print(f"[graph] {step.get('event')}: {step.get('summary', '')}", file=sys.stderr, flush=True)

from pydantic import BaseModel, Field

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from app.compliance.agentic.toolbox import ContextToolbox
from app.compliance.models import RuleEvaluation
from app.compliance.rules.registry import AuditRule
from app.core.ports.llm import LLMProvider

logger = logging.getLogger(__name__)


# ── State ────────────────────────────────────────────────────


class SectionChunk(TypedDict):
    document_type: str
    section_type: str | None
    pages: list[dict]
    chunk_id: str


class WorkerResult(TypedDict):
    chunk_id: str
    status: str
    confidence: float
    reasoning: str
    evidence: str
    page_range: str
    section_type: str | None


class AgenticAuditState(TypedDict):
    rule: AuditRule
    all_extractions: list[dict]
    section_map: dict[int, dict]
    llm: LLMProvider
    doc_id: str
    page_cap: int
    worker_page_limit: int
    max_concurrent: int
    max_tool_calls: int
    # Injected per-worker by fan_out_workers via Send:
    toolbox: ContextToolbox | None
    current_chunk: SectionChunk | None
    # Accumulated across workers:
    worker_results: Annotated[list[WorkerResult], operator.add]
    final_evaluation: RuleEvaluation | None


# ── Pydantic models for LLM structured output ───────────────


class WorkerVerdict(BaseModel):
    status: Literal["compliant", "non_compliant", "uncertain", "not_applicable"]
    confidence: float
    reasoning: str = Field(
        description=(
            "1-3 sentences explaining the verdict. "
            "MUST be consistent with status: non_compliant reasoning names what is missing "
            "or wrong (citing PAGE:N); compliant reasoning states what was found satisfying "
            "the criterion. Never write reasoning that concludes the opposite of your status."
        )
    )
    evidence: str = Field(
        description=(
            "Factual prose (2-4 sentences, max 300 chars) citing what was found and where. "
            "Name the document type and use PAGE:N inline, e.g. "
            "'The BPCR on PAGE:3 lists batch size as 400 kg. "
            "The RAW MATERIAL REQUEST form (PAGE:36) records the same quantity.' "
            "Do not restate the rule, verdict, or confidence."
        )
    )


class WorkerAction(BaseModel):
    action: Literal["get_context_summary", "get_context_pages", "produce_verdict"]
    document_type: str = ""
    section_type: str | None = None
    page_nums: list[int] = Field(default_factory=list)
    verdict: WorkerVerdict | None = None


class SynthesisOutput(BaseModel):
    status: Literal["compliant", "non_compliant", "uncertain", "not_applicable"]
    confidence: float
    reasoning: str = Field(
        description=(
            "1-3 sentences synthesising the overall package verdict. "
            "MUST be consistent with status: if non_compliant, name the specific section or gap "
            "that drives the failure (cite PAGE:N); if compliant, confirm what was verified "
            "across sections. Never contradict your chosen status in the reasoning text."
        )
    )
    evidence: str = Field(
        description=(
            "Factual prose (3-5 sentences, max 600 chars) summarising what was verified across all sections. "
            "Name document types and use PAGE:N inline. State specific values, field names, and matches or gaps found. "
            "Example style: 'The BPCR on PAGE:3 lists batch size as 400 kg. "
            "The RAW MATERIAL REQUEST (PAGE:36) records 400.000 kg, confirming the match. "
            "The ALLOCATED form (PAGE:39) records issuance for the batch.' "
            "Do not restate the verdict, confidence, or section names — only the factual findings."
        )
    )


# ── System prompts ────────────────────────────────────────────

_WORKER_SYSTEM = (
    "You are a pharmaceutical compliance agent evaluating document sections. "
    "Use available tools to gather context, then produce a verdict. "
    "In the evidence field write factual prose: name the document type and cite PAGE:N inline "
    "for every specific value, field, or signature you observed. Do not restate the rule or verdict."
)

_SYNTHESIZER_SYSTEM = (
    "You are a senior pharmaceutical compliance reviewer synthesising section-level findings. "
    "In the evidence field write factual prose (max 600 chars): summarise what was actually found "
    "across documents, naming each document type and citing PAGE:N inline for every value, field, "
    "or match/gap you reference. Do not restate verdict, confidence, or section names."
)


# ── Private helpers ───────────────────────────────────────────


def _group_by_section(
    all_extractions: list[dict],
    section_map: dict[int, dict],
    rule: AuditRule,
) -> dict[tuple[str, str | None], list[dict]]:
    groups: dict[tuple[str, str | None], list[dict]] = defaultdict(list)
    for ext in all_extractions:
        p = ext.get("page_num", 0)
        meta = section_map.get(p, {})
        doc_type = meta.get("document_type", "")
        sec_type = meta.get("section_type")
        if not doc_type or doc_type not in rule.applicable_document_types:
            continue
        if rule.applicable_section_types and sec_type not in rule.applicable_section_types:
            continue
        groups[(doc_type, sec_type)].append(ext)
    return groups


def _chunk_sections(
    groups: dict[tuple[str, str | None], list[dict]],
    worker_page_limit: int,
) -> list[SectionChunk]:
    chunks: list[SectionChunk] = []
    for (doc_type, sec_type), pages in groups.items():
        if len(pages) <= worker_page_limit:
            chunks.append(SectionChunk(
                document_type=doc_type,
                section_type=sec_type,
                pages=pages,
                chunk_id=f"{sec_type or doc_type}-0",
            ))
        else:
            mid = len(pages) // 2
            chunks.append(SectionChunk(
                document_type=doc_type,
                section_type=sec_type,
                pages=pages[:mid],
                chunk_id=f"{sec_type or doc_type}-0",
            ))
            chunks.append(SectionChunk(
                document_type=doc_type,
                section_type=sec_type,
                pages=pages[mid:],
                chunk_id=f"{sec_type or doc_type}-1",
            ))
    return chunks


def _build_initial_prompt(
    rule: AuditRule,
    chunk: SectionChunk,
    section_content: str,
    page_range: str,
    preloaded_context: list[tuple[str, str, str]] | None = None,
) -> str:
    """Build the worker's initial prompt.

    preloaded_context: list of (label, doc_type, content) tuples already fetched
    so the agent doesn't need to call tools for the initial cross-document lookup.
    """
    sec_type = chunk["section_type"] or chunk["document_type"]

    preloaded_text = ""
    if preloaded_context:
        blocks = []
        for label, doc_type, content in preloaded_context:
            blocks.append(f"--- {label} ---\n{content}")
        preloaded_text = (
            "CROSS-DOCUMENT CONTEXT (pre-loaded for this rule):\n\n"
            + "\n\n".join(blocks)
            + "\n\n"
        )

    return (
        f"You are a pharmaceutical compliance agent evaluating one section of a document package.\n\n"
        f"RULE: {rule.text}\n"
        f"COMPLIANCE CRITERION: {rule.pass_criteria}\n\n"
        f"PRIMARY SECTION ({sec_type}, {page_range}):\n{section_content}\n\n"
        f"{preloaded_text}"
        # Tools are disabled: all context is pre-loaded as raw pages above.
        # f"Available tools (use if you need additional details beyond the context above):\n"
        # f"- get_context_summary(document_type, section_type?) → summary of a cross-document section\n"
        # f"- get_context_pages(document_type, section_type?, page_nums?) → raw pages from a cross-document\n\n"
        f"Instructions:\n"
        f"1. Read the primary section content and the pre-loaded cross-document context above.\n"
        f"2. When you have enough evidence, call produce_verdict with your determination.\n"
        f"   In the evidence field write factual prose (max 300 chars): name each document and "
        f"cite PAGE:N inline for every value, field, or signature observed — e.g. 'The BPCR on "
        f"PAGE:3 lists batch size as 400 kg. The RAW MATERIAL REQUEST (PAGE:36) records 400.000 kg, "
        f"confirming the match.' Do NOT restate the rule or verdict.\n"
        f"   NOTE: 'Step No.' values in the primary section are manufacturing step numbers, "
        f"not page numbers. Do not use them as page_nums in tool calls."
    )


# ── Routing function ──────────────────────────────────────────


def fan_out_workers(state: AgenticAuditState) -> list[Send]:
    rule = state["rule"]
    toolbox = ContextToolbox(
        state["all_extractions"],
        state["section_map"],
        state["doc_id"],
        state["page_cap"],
    )
    groups = _group_by_section(state["all_extractions"], state["section_map"], rule)
    chunks = _chunk_sections(groups, state["worker_page_limit"])

    if not chunks:
        return [Send("synthesize", state)]
    return [
        Send("section_worker", {**state, "current_chunk": chunk, "toolbox": toolbox})
        for chunk in chunks
    ]


# ── Nodes ─────────────────────────────────────────────────────


async def section_worker(state: AgenticAuditState) -> dict:
    chunk: SectionChunk = state["current_chunk"]
    rule = state["rule"]
    toolbox: ContextToolbox = state["toolbox"]
    llm = state["llm"]
    max_calls = state["max_tool_calls"]

    pages = chunk["pages"]
    page_range = f"pp. {pages[0].get('page_num', '?')}-{pages[-1].get('page_num', '?')}"
    section_content = "\n\n".join(
        f"[p{p.get('page_num', '?')}]\n{str(p.get('markdown', '') or '')[:8000]}"
        for p in pages
    )

    # Pre-fetch cross-document context so the agent starts with it in the prompt.
    # Always use raw pages so no detail is lost to summarisation.
    preloaded_context: list[tuple[str, str, str]] = []
    if rule.context_sources:
        for src in rule.context_sources:
            doc_type = src.get("document_type", "")
            sec_types = src.get("section_types") or [None]
            for sec_type_src in sec_types:
                label = f"{doc_type}" + (f"/{sec_type_src}" if sec_type_src else "")
                content = toolbox.get_context_pages(doc_type, sec_type_src)
                if content:
                    preloaded_context.append((label, doc_type, content))
                    _trace(rule.id, {
                        "event": "preload_context",
                        "label": label,
                        "chars": len(content),
                        "summary": f"pre-loaded {label!r}: {len(content)} chars",
                    })

    conversation = _build_initial_prompt(rule, chunk, section_content, page_range, preloaded_context)
    _trace(rule.id, {
        "event": "worker_start", "chunk_id": chunk["chunk_id"],
        "page_range": page_range,
        "preloaded_sources": [lbl for lbl, _, _ in preloaded_context],
        "summary": f"chunk={chunk['chunk_id']} {page_range} preloaded={len(preloaded_context)} sources",
    })
    tool_calls = 0

    while tool_calls < max_calls:
        action = await llm.generate_structured(conversation, WorkerAction, system=_WORKER_SYSTEM)
        if action.action == "produce_verdict" and action.verdict:
            v = action.verdict
            _trace(rule.id, {"event": "verdict", "status": v.status, "confidence": v.confidence,
                             "reasoning": v.reasoning[:300], "evidence": v.evidence[:300],
                             "summary": f"verdict={v.status} conf={v.confidence:.2f}"})
            return {"worker_results": [WorkerResult(
                chunk_id=chunk["chunk_id"],
                status=v.status,
                confidence=v.confidence,
                reasoning=v.reasoning,
                evidence=v.evidence,
                page_range=page_range,
                section_type=chunk["section_type"],
            )]}
        elif action.action == "get_context_summary":
            # Defensive branch: the prompt's "Available tools" block is
            # commented out (010 chose to pre-load all raw pages instead
            # of asking the LLM to fetch them), but ``WorkerAction``'s
            # Literal still allows this action. If the LLM still picks
            # it we serve the result AND fire telemetry so the prompt-
            # vs-model mismatch is observable rather than silent.
            result = toolbox.get_context_summary(action.document_type, action.section_type)
            _trace(rule.id, {"event": "get_context_summary",
                             "doc_type": action.document_type, "sec_type": action.section_type,
                             "result_chars": len(result), "result_preview": result[:200],
                             "summary": f"get_context_summary({action.document_type!r}, {action.section_type!r}) → {len(result)} chars"})
            try:
                from app.observability.run_telemetry import record_event  # noqa: PLC0415
                record_event(
                    "agentic.unexpected_tool_call",
                    level="warning",
                    rule_id=rule.id,
                    action="get_context_summary",
                    note=(
                        "LLM chose a tool action that the current "
                        "worker prompt does not advertise. Either the "
                        "model is improvising or the prompt needs to "
                        "re-document the tools."
                    ),
                )
            except Exception:  # pragma: no cover — never break eval
                pass
            conversation += (
                f"\n\nTOOL: get_context_summary({action.document_type})\n"
                f"RESULT:\n{result or '[No summary available]'}\n"
            )
        elif action.action == "get_context_pages":
            result = toolbox.get_context_pages(
                action.document_type,
                action.section_type,
                action.page_nums or None,
            )
            _trace(rule.id, {"event": "get_context_pages",
                             "doc_type": action.document_type, "sec_type": action.section_type,
                             "page_nums": action.page_nums, "result_chars": len(result),
                             "result_preview": result[:200],
                             "summary": f"get_context_pages({action.document_type!r}, {action.section_type!r}) → {len(result)} chars"})
            try:
                from app.observability.run_telemetry import record_event  # noqa: PLC0415
                record_event(
                    "agentic.unexpected_tool_call",
                    level="warning",
                    rule_id=rule.id,
                    action="get_context_pages",
                    note=(
                        "LLM chose a tool action that the current "
                        "worker prompt does not advertise. Either the "
                        "model is improvising or the prompt needs to "
                        "re-document the tools."
                    ),
                )
            except Exception:  # pragma: no cover — never break eval
                pass
            conversation += (
                f"\n\nTOOL: get_context_pages({action.document_type})\n"
                f"RESULT:\n{result or '[No pages found]'}\n"
            )
        tool_calls += 1

    # Exhausted tool call limit — force verdict
    _trace(rule.id, {"event": "tool_limit_exhausted", "tool_calls": tool_calls,
                     "summary": f"tool call limit {tool_calls} reached, forcing verdict"})
    forced = await llm.generate_structured(
        conversation + "\n\nYou have reached the tool call limit. Produce your verdict now.",
        WorkerVerdict,
        system=_WORKER_SYSTEM,
    )
    return {"worker_results": [WorkerResult(
        chunk_id=chunk["chunk_id"],
        status=forced.status,
        confidence=forced.confidence,
        reasoning=forced.reasoning,
        evidence=forced.evidence,
        page_range=page_range,
        section_type=chunk["section_type"],
    )]}


async def synthesize(state: AgenticAuditState) -> dict:
    rule = state["rule"]
    worker_results = state["worker_results"]

    if not worker_results:
        evaluation = RuleEvaluation(
            rule_id=rule.id,
            status="uncertain",
            confidence=0.5,
            reasoning="No primary document pages found matching applicable_document_types.",
            evidence="",
        )
        return {"final_evaluation": evaluation}

    if all(r["status"] == "uncertain" for r in worker_results):
        evaluation = RuleEvaluation(
            rule_id=rule.id,
            status="uncertain",
            confidence=min(r["confidence"] for r in worker_results),
            reasoning="All section workers returned uncertain verdicts.",
            evidence="\n".join(r["evidence"] for r in worker_results),
        )
        return {"final_evaluation": evaluation}

    workers_text = "\n\n".join(
        f"Section: {r['section_type'] or 'unknown'} ({r['page_range']})\n"
        f"Status: {r['status']} (confidence: {r['confidence']:.2f})\n"
        f"Reasoning: {r['reasoning']}\n"
        f"Evidence: {r['evidence']}"
        for r in worker_results
    )
    prompt = (
        f"RULE: {rule.text}\nCRITERION: {rule.pass_criteria}\n\n"
        f"SECTION VERDICTS:\n{workers_text}\n\n"
        "Synthesize into a single package-level verdict. "
        "Reason only from the provided verdicts — do not introduce new analysis. "
        "If any section is non_compliant with high confidence, the package is non_compliant.\n\n"
        "CRITICAL: Your reasoning and status MUST be consistent. "
        "If status=non_compliant, reasoning must state what is non_compliant — "
        "do NOT write a conclusion saying the package is compliant. "
        "If status=compliant, reasoning must confirm what was verified across sections.\n\n"
        "For the evidence field: write factual prose (max 600 chars) summarising the concrete "
        "findings from the section evidence above — document names, PAGE:N references, specific "
        "values, and any matches or gaps. Do not restate the verdict, confidence, or section labels."
    )

    result = await state["llm"].generate_structured(
        prompt,
        SynthesisOutput,
        system=_SYNTHESIZER_SYSTEM,
    )
    evaluation = RuleEvaluation(
        rule_id=rule.id,
        status=result.status,
        confidence=result.confidence,
        reasoning=result.reasoning,
        evidence=result.evidence,
    )
    return {"final_evaluation": evaluation}


# ── Graph assembly ────────────────────────────────────────────


def build_agentic_graph() -> CompiledStateGraph:
    builder = StateGraph(AgenticAuditState)
    builder.add_node("section_worker", section_worker)
    builder.add_node("synthesize", synthesize)
    builder.add_conditional_edges(START, fan_out_workers, ["section_worker", "synthesize"])
    builder.add_edge("section_worker", "synthesize")
    builder.add_edge("synthesize", END)
    return builder.compile()


_AGENTIC_GRAPH: CompiledStateGraph | None = None


def get_agentic_graph() -> CompiledStateGraph:
    global _AGENTIC_GRAPH
    if _AGENTIC_GRAPH is None:
        _AGENTIC_GRAPH = build_agentic_graph()
    return _AGENTIC_GRAPH
