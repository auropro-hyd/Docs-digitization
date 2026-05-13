"""GMP compliance review agent.

Thin wrapper over the shared RuleBatchEvaluator.
"""

from __future__ import annotations

import logging

from app.compliance.agentic.postpass import run_agentic_postpass
from app.compliance.evaluator import assemble_agent_report, run_agent_evaluation
from app.compliance.models import AgentReport
from app.compliance.rules.registry import RuleRegistry
from app.config.settings import ComplianceConfig
from app.core.ports.llm import LLMProvider
from app.core.ports.vlm import VLMProvider

logger = logging.getLogger(__name__)

AGENT_NAME = "gmp"


class GMPAgent:
    def __init__(
        self,
        llm: LLMProvider,
        registry: RuleRegistry,
        config: ComplianceConfig,
        vlm: VLMProvider | None = None,
    ):
        self._llm = llm
        self._registry = registry
        self._config = config
        self._vlm = vlm

    async def review_document(
        self,
        extractions: list[dict],
        progress_callback=None,
        prescreen_callback=None,
        section_map: dict[int, dict] | None = None,
        global_kv_pairs: list[dict] | None = None,
        doc_id: str | None = None,
    ) -> AgentReport:
        batches = self._registry.get_batches(
            AGENT_NAME,
            self._config.rule_batch_size,
            self._config.batch_by_category,
            scope_filter="page",
        )
        all_rules = self._registry.get_rules(AGENT_NAME)
        pages = [ext.get("page_num", 0) for ext in extractions]

        results = await run_agent_evaluation(
            AGENT_NAME,
            batches,
            extractions,
            self._llm,
            max_concurrent=self._config.max_concurrent_batches,
            progress_callback=progress_callback,
            prescreen_callback=prescreen_callback,
            section_map=section_map,
            global_kv_pairs=global_kv_pairs,
            vlm=self._vlm,
            doc_id=doc_id,
        )

        agentic_results = await run_agentic_postpass(
            AGENT_NAME, self._registry, extractions,
            section_map or {}, self._llm, self._config,
            doc_id=doc_id or "",
            progress_callback=progress_callback,
        )
        results = results + agentic_results
        return assemble_agent_report(AGENT_NAME, all_rules, results, pages)
