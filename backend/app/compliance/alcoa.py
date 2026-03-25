"""ALCOA++ compliance review agent.

Thin wrapper over the shared RuleBatchEvaluator.  Evaluates 82 rules
across 9 ALCOA++ categories using the rule-batch map-reduce pattern.
"""

from __future__ import annotations

import logging

from app.compliance.evaluator import (
    assemble_agent_report,
    run_agent_evaluation,
    run_document_scope_evaluation,
)
from app.compliance.models import AgentReport
from app.compliance.rules.registry import RuleRegistry
from app.config.settings import ComplianceConfig
from app.core.ports.llm import LLMProvider

logger = logging.getLogger(__name__)

AGENT_NAME = "alcoa"


class ALCOAAgent:
    def __init__(self, llm: LLMProvider, registry: RuleRegistry, config: ComplianceConfig):
        self._llm = llm
        self._registry = registry
        self._config = config

    async def review_document(
        self,
        extractions: list[dict],
        progress_callback=None,
        prescreen_callback=None,
        section_map: dict[int, dict] | None = None,
        global_kv_pairs: list[dict] | None = None,
    ) -> AgentReport:
        page_batches = self._registry.get_batches(
            AGENT_NAME,
            self._config.rule_batch_size,
            self._config.batch_by_category,
            scope_filter="page",
        )
        doc_batches = self._registry.get_batches(
            AGENT_NAME,
            self._config.rule_batch_size,
            self._config.batch_by_category,
            scope_filter="document",
        )
        all_rules = self._registry.get_rules(AGENT_NAME)
        pages = [ext.get("page_num", 0) for ext in extractions]

        page_results = await run_agent_evaluation(
            AGENT_NAME,
            page_batches,
            extractions,
            self._llm,
            max_concurrent=self._config.max_concurrent_batches,
            progress_callback=progress_callback,
            prescreen_callback=prescreen_callback,
            section_map=section_map,
            global_kv_pairs=global_kv_pairs,
        )

        doc_results = await run_document_scope_evaluation(
            AGENT_NAME, doc_batches, extractions, self._llm,
        ) if doc_batches else []

        all_results = page_results + doc_results
        return assemble_agent_report(AGENT_NAME, all_rules, all_results, pages)
