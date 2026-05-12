"""Cross-page evaluator: evaluates rules against multi-section content.

Each LLM call receives content from multiple document sections and evaluates
a batch of cross-page rules using the "show your work" prompting pattern.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from app.compliance.models import (
    CrossReference,
    DocumentSegmentation,
    RuleBatchResult,
    RuleEvaluation,
    SectionResolution,
)
from app.compliance.rules.registry import AuditRule
from app.config.settings import get_settings
from app.core.ports.llm import LLMProvider

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a cross-page compliance reviewer for pharmaceutical documents. "
    "You compare data across different sections of the same document packet to "
    "verify consistency, completeness, and accuracy. "
    "You MUST respond with valid JSON matching the provided schema."
)


def _build_cross_page_prompt(
    rules: list[AuditRule],
    sections_content: dict[str, str],
    section_meta: dict[str, dict],
    dependency_tags: list[dict] | None = None,
) -> str:
    rules_text = "\n".join(
        f"- [{r.id}] {r.text}" for r in rules
    )

    sections_text = ""
    for sid, content in sections_content.items():
        meta = section_meta.get(sid, {})
        name = meta.get("name", sid)
        stype = meta.get("section_type", "unknown")
        start = meta.get("start_page", "?")
        end = meta.get("end_page", "?")
        sections_text += (
            f"\n--- Section: {name} (type: {stype}, pages {start}-{end}) ---\n"
            f"{content}\n"
            f"--- End Section ---\n"
        )

    dep_text = "None available"
    if dependency_tags:
        dep_text = "\n".join(
            f"- [{d.get('ref_type', '?')}] {d.get('identifier', '?')} "
            f"(page {d.get('page_num', '?')}): {d.get('context', '')[:200]}"
            for d in dependency_tags[:50]
        )

    return (
        f"Evaluate the following cross-page consistency rules.\n"
        f"You have content from MULTIPLE sections of the same document.\n\n"
        f"For EACH rule, you MUST:\n"
        f"1. EXTRACT: List ALL relevant data points from EACH section separately.\n"
        f"   Quote the exact text/values found. Note which page and section.\n"
        f"2. COMPARE: Show your comparison step by step.\n"
        f"   For numerical values: list each number, compute sums explicitly.\n"
        f"   For identifiers: list each occurrence, note matches and mismatches.\n"
        f"   For completeness: list what is required and check each item.\n"
        f"3. JUDGE: Determine compliance status based on your comparison.\n"
        f"4. CONFIDENCE: Rate 0.0-1.0 based on evidence quality.\n\n"
        f"Return status as: compliant, non_compliant, not_applicable, or uncertain.\n\n"
        f"For EVERY rule (including compliant ones), you MUST provide:\n"
        f"  reasoning: 1-3 sentence explanation of WHY you reached this verdict, "
        f"referencing specific data points or sections.\n"
        f"  evidence: Put your full extraction and comparison trail here. For compliant rules, "
        f"cite the text that proves compliance. For non_compliant, cite the conflicting data.\n\n"
        f"RULES TO EVALUATE:\n{rules_text}\n\n"
        f"SECTIONS:\n{sections_text}\n\n"
        f"DEPENDENCY CUES (hints from per-page analysis — use to focus, not as sole evidence):\n"
        f"{dep_text}"
    )


def group_rules_by_sections(
    rules: list[AuditRule],
    resolutions: list[SectionResolution],
) -> dict[tuple[str, ...], list[AuditRule]]:
    """Group rules that share the same resolved section set for efficient batching."""
    resolution_map = {r.rule_id: r for r in resolutions}
    groups: dict[tuple[str, ...], list[AuditRule]] = defaultdict(list)
    for rule in rules:
        res = resolution_map.get(rule.id)
        if res and res.applicable and res.matched_section_ids:
            key = tuple(sorted(res.matched_section_ids))
            groups[key].append(rule)
    return dict(groups)


def gather_section_content(
    section_ids: tuple[str, ...],
    extractions: list[dict],
    segmentation: DocumentSegmentation,
    max_chars: int | None = None,
) -> tuple[dict[str, str], dict[str, dict]]:
    """Collect markdown content for the requested sections."""
    max_chars = max_chars or get_settings().compliance.max_section_chars

    sec_map = {s.section_id: s for s in segmentation.sections}
    content: dict[str, str] = {}
    meta: dict[str, dict] = {}

    for sid in section_ids:
        sec = sec_map.get(sid)
        if not sec:
            continue
        meta[sid] = {
            "name": sec.name,
            "section_type": sec.section_type,
            "start_page": sec.start_page,
            "end_page": sec.end_page,
        }
        pages = [
            ext.get("markdown", "")
            for ext in extractions
            if sec.start_page <= ext.get("page_num", 0) <= sec.end_page
        ]
        full = "\n\n".join(pages)
        if len(full) > max_chars:
            full = full[:max_chars] + "\n\n[... content truncated for context window ...]"
        content[sid] = full

    return content, meta


class CrossPageEvaluator:
    """Evaluates cross-page rules against multi-section content."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def evaluate(
        self,
        rules: list[AuditRule],
        sections_content: dict[str, str],
        section_meta: dict[str, dict],
        dependency_tags: list[dict] | None = None,
    ) -> RuleBatchResult:
        if not rules or not sections_content:
            return RuleBatchResult(evaluations=[
                RuleEvaluation(rule_id=r.id, status="not_applicable")
                for r in rules
            ])

        prompt = _build_cross_page_prompt(
            rules, sections_content, section_meta, dependency_tags,
        )

        try:
            result = await self._llm.generate_structured(
                prompt, RuleBatchResult, system=_SYSTEM,
            )
            if not isinstance(result, RuleBatchResult):
                result = RuleBatchResult.model_validate(result)
        except Exception:
            logger.exception("Cross-page evaluation failed for %d rules", len(rules))
            return RuleBatchResult(evaluations=[
                RuleEvaluation(
                    rule_id=r.id,
                    status="uncertain",
                    description="Cross-page evaluation failed",
                )
                for r in rules
            ])

        # Silent-drop guard: the LLM sometimes returns a partial
        # evaluations list — REC-MAN2 vanished from run e5e35ffc-…'s
        # report this way: requested in the prompt, omitted from the
        # response, never surfaced anywhere downstream. Reconcile the
        # returned evaluations against the requested rule_ids and
        # backfill any missing rule with status="error" so the
        # operator sees the drop instead of silently losing a rule's
        # verdict. Telemetry event lets post-run analysis flag
        # which rules / which models are dropping.
        requested_ids = {r.id for r in rules}
        returned_ids = {ev.rule_id for ev in result.evaluations}
        missing = sorted(requested_ids - returned_ids)
        if missing:
            logger.warning(
                "cross_page.llm_dropped_rules — requested=%d returned=%d "
                "dropped=%s (rule_ids backfilled as status=error)",
                len(requested_ids), len(returned_ids), missing,
            )
            try:
                from app.observability.run_telemetry import record_event
                record_event(
                    "cross_page.rule_dropped_by_llm",
                    level="warning",
                    requested_count=len(requested_ids),
                    returned_count=len(returned_ids),
                    dropped_rule_ids=missing,
                )
            except Exception:  # pragma: no cover — never break eval
                pass
            backfilled = list(result.evaluations)
            for rule_id in missing:
                backfilled.append(RuleEvaluation(
                    rule_id=rule_id,
                    status="error",
                    description=(
                        "Rule was sent to the cross-page LLM but the "
                        "response omitted it — verdict lost. The model "
                        "silently dropped this rule from its evaluations "
                        "list. Re-run or inspect the prompt to surface "
                        "the underlying cause."
                    ),
                ))
            result = RuleBatchResult(evaluations=backfilled)

        return result
