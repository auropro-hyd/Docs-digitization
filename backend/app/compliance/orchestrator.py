"""Compliance orchestrator agent.

Determines document relevance and routes to applicable sub-agents.
Uses GPT-5-mini (configurable) for reasoning about document type.
"""

from __future__ import annotations

import logging

from app.compliance.models import OrchestratorResult
from app.core.ports.llm import LLMProvider

logger = logging.getLogger(__name__)

_ORCHESTRATOR_SYSTEM = (
    "You are a pharmaceutical compliance routing agent. "
    "Your job is to analyze a document and determine which compliance audits are applicable. "
    "You MUST respond with valid JSON matching the provided schema."
)

_ORCHESTRATOR_PROMPT = """Analyze this pharmaceutical document and determine:

1. **Document type**: Is this a batch_record, sop, protocol, certificate, logbook, or other?
2. **Relevance**: Is this a pharmaceutical/GxP document suitable for compliance audit?
3. **Applicable audit categories**: Which of these apply?
   - alcoa: ALCOA++ data integrity review (applicable to almost all GxP records)
   - gmp: GMP validation review (equipment IDs, SOP refs, environmental data, corrections)
   - checklist: Checklist completeness review (checkboxes, signatures, dates, blank fields)
   - sop: SOP compliance review (SOP references, step alignment, deviation documentation)

4. **Skipped categories**: For any NOT applicable, explain why.

DOCUMENT METADATA:
- Filename: {filename}
- Total pages: {total_pages}

FIRST 3 PAGES CONTENT (summary):
{content_preview}

KEY-VALUE PAIRS EXTRACTED:
{kv_pairs}
"""


class ComplianceOrchestrator:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def analyze(
        self,
        filename: str,
        total_pages: int,
        extractions: list[dict],
        key_value_pairs: list[dict] | None = None,
    ) -> OrchestratorResult:
        preview_pages = extractions[:3]
        content_preview = "\n\n---\n\n".join(
            f"Page {ext.get('page_num', '?')}:\n{ext.get('markdown', '')[:2000]}"
            for ext in preview_pages
        )

        kv_text = "None extracted"
        if key_value_pairs:
            kv_text = "\n".join(
                f"- {kv.get('key', '?')}: {kv.get('value', '?')}"
                for kv in key_value_pairs[:20]
            )

        prompt = _ORCHESTRATOR_PROMPT.format(
            filename=filename,
            total_pages=total_pages,
            content_preview=content_preview,
            kv_pairs=kv_text,
        )

        try:
            result = await self._llm.generate_structured(
                prompt, OrchestratorResult, system=_ORCHESTRATOR_SYSTEM,
            )
            if not isinstance(result, OrchestratorResult):
                result = OrchestratorResult.model_validate(result)
            return result
        except Exception:
            logger.exception("Orchestrator analysis failed, defaulting to all agents")
            return OrchestratorResult(
                is_relevant=True,
                confidence=0.5,
                document_type="batch_record",
                document_type_reasoning="Orchestrator failed; running all agents as fallback.",
                applicable_categories=["alcoa", "gmp", "checklist", "sop"],
                skipped_categories=[],
            )
