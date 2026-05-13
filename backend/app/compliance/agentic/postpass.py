"""Adapter: runs agentic_audit rules as a post-pass and returns results
in the same (batch_id, page_num, RuleBatchResult) format as run_agent_evaluation()."""

from __future__ import annotations

import asyncio
import logging

from app.compliance.agentic.graph import AgenticAuditState, get_agentic_graph
from app.compliance.models import RuleBatchResult, RuleEvaluation
from app.compliance.rules.registry import AuditRule, RuleBatch, RuleRegistry
from app.config.settings import ComplianceConfig
from app.core.ports.llm import LLMProvider

logger = logging.getLogger(__name__)


async def run_agentic_postpass(
    agent_name: str,
    registry: RuleRegistry,
    extractions: list[dict],
    section_map: dict[int, dict],
    llm: LLMProvider,
    config: ComplianceConfig,
    doc_id: str,
    progress_callback=None,
) -> list[tuple[str, int | None, RuleBatchResult]]:
    """Evaluate all agentic_audit rules for an agent.

    Returns results in the same (batch_id, page_num, RuleBatchResult) format
    used by run_agent_evaluation() so callers can simply extend their results list.
    """
    agentic_rules: list[AuditRule] = [
        r for r in registry.get_rules(agent_name)
        if r.evaluation_strategy == "agentic_audit" and r.context_sources
    ]
    if not agentic_rules:
        return []

    graph = get_agentic_graph()
    results: list[tuple[str, int | None, RuleBatchResult]] = []

    semaphore = asyncio.Semaphore(config.max_concurrent_batches)

    async def _run_one(rule: AuditRule) -> tuple[str, int | None, RuleBatchResult]:
        batch_id = f"agentic-{rule.id}"
        try:
            async with semaphore:
                initial_state = AgenticAuditState(
                    rule=rule,
                    all_extractions=extractions,
                    section_map=section_map,
                    llm=llm,
                    doc_id=doc_id,
                    page_cap=config.agentic_page_cap,
                    worker_page_limit=config.agentic_worker_page_limit,
                    max_concurrent=config.max_concurrent_batches,
                    max_tool_calls=config.agentic_max_tool_calls,
                    toolbox=None,
                    worker_results=[],
                    final_evaluation=None,
                    current_chunk=None,
                )
                final_state = await graph.ainvoke(initial_state)
                evaluation: RuleEvaluation = final_state.get("final_evaluation") or RuleEvaluation(
                    rule_id=rule.id,
                    status="uncertain",
                    reasoning="Agentic graph returned no evaluation.",
                )
        except Exception as exc:
            logger.error("Agentic rule %s failed: %s", rule.id, exc, exc_info=True)
            evaluation = RuleEvaluation(
                rule_id=rule.id,
                status="uncertain",
                reasoning=f"Agentic evaluation error: {exc}",
            )
        return (batch_id, None, RuleBatchResult(evaluations=[evaluation]))

    rule_by_id = {r.id: r for r in agentic_rules}
    tasks = [_run_one(r) for r in agentic_rules]
    completed_count = 0

    for coro in asyncio.as_completed(tasks):
        result = await coro
        results.append(result)
        completed_count += 1

        if progress_callback:
            batch_id = result[0]
            completed_rule_id = batch_id.removeprefix("agentic-")
            completed_rule = rule_by_id.get(completed_rule_id, agentic_rules[0])
            batch = RuleBatch(
                batch_id=batch_id,
                category=completed_rule.category,
                agent=agent_name,
                rules=[completed_rule],
            )
            await progress_callback(completed_count, len(agentic_rules), batch, result)

    return results
