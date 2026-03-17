"""Section resolver: LLM maps rule [sections: ...] intents to actual document sections.

A single LLM call resolves semantic section requirements (e.g. "manufacturing",
"raw_material") to the concrete section_ids produced by segmentation.
"""

from __future__ import annotations

import logging

from app.compliance.models import (
    DocumentSegmentation,
    SectionResolution,
    SectionResolutionResult,
)
from app.compliance.rules.registry import AuditRule
from app.core.ports.llm import LLMProvider

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a document structure analyst. Your job is to map semantic section "
    "requirements from audit rules to the actual sections found in a document. "
    "You MUST respond with valid JSON matching the provided schema."
)


class SectionResolver:
    """Resolves cross-page rule section requirements to actual document sections."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def resolve(
        self,
        rules: list[AuditRule],
        segmentation: DocumentSegmentation,
    ) -> SectionResolutionResult:
        sections_desc = "\n".join(
            f"- {s.section_id}: \"{s.name}\" (type: {s.section_type}, "
            f"pages {s.start_page}-{s.end_page}): {s.description}"
            for s in segmentation.sections
        )

        rules_desc = "\n".join(
            f"- {r.id}: \"{r.text}\" [sections: {', '.join(r.context_sections)}]"
            for r in rules
            if r.context_sections
        )

        if not rules_desc:
            return SectionResolutionResult(resolutions=[])

        prompt = (
            f"Given this document's section structure:\n{sections_desc}\n\n"
            f"And these cross-page rules, each with a [sections: ...] tag indicating "
            f"the TYPES of sections needed (semantic descriptions, not exact matches):\n"
            f"{rules_desc}\n\n"
            f"For each rule, determine which actual document sections are relevant.\n"
            f"If a rule's section types don't exist in this document, set applicable=false.\n"
            f"If [sections: *], all sections are relevant.\n\n"
            f"Return one resolution per rule with: rule_id, matched_section_ids, "
            f"applicable (bool), and reason (brief explanation)."
        )

        try:
            result = await self._llm.generate_structured(
                prompt, SectionResolutionResult, system=_SYSTEM,
            )
            if not isinstance(result, SectionResolutionResult):
                result = SectionResolutionResult.model_validate(result)
            return result
        except Exception:
            logger.exception("Section resolution failed, marking all rules applicable with all sections")
            all_ids = [s.section_id for s in segmentation.sections]
            return SectionResolutionResult(
                resolutions=[
                    SectionResolution(
                        rule_id=r.id,
                        matched_section_ids=all_ids,
                        applicable=True,
                        reason="Resolution failed; using all sections as fallback",
                    )
                    for r in rules if r.context_sections
                ],
            )
