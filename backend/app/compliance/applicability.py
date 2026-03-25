"""Pre-evaluation applicability gate.

Two-tier hybrid gate that filters rules before full LLM evaluation:

  Tier 1 (static): Instantly skips rules with ``evaluation_mode == "cannot_evaluate"``
                    (e.g. rules needing training matrix or external data).

  Tier 2 (LLM pre-screen): A single lightweight LLM call per page determines
                            which remaining rules are actually relevant to
                            the page content — replacing the brittle static
                            keyword / page-type filters with content-aware
                            classification.

When ``applicability_mode`` is ``"static"``, the original 4-stage static
filter chain is used instead (no LLM cost, less accurate).
"""

from __future__ import annotations

import logging

from app.compliance.models import ApplicabilityScreenResult, RuleEvaluation
from app.compliance.rules.registry import AuditRule
from app.core.ports.llm import LLMProvider

logger = logging.getLogger(__name__)

_PRESCREEN_MAX_CONTENT_CHARS = 1200

_PRESCREEN_SYSTEM = (
    "You are a pharmaceutical compliance triage specialist. "
    "Your job is to quickly determine which audit rules are relevant to "
    "a given page of a batch production record. Be conservative — if in "
    "doubt, include the rule (it's better to evaluate than to miss). "
    "IMPORTANT: The content is OCR-extracted from scanned documents. "
    "Handwritten text may appear garbled (e.g., names like 'Noga', 'N088', "
    "'staten' are OCR reads of handwritten signatures). Dates may have wrong "
    "year digits (2015 instead of 2025). Dashes (-/---) in form fields mean "
    "'not applicable'. These are OCR artifacts, not document quality issues. "
    "Rules about legibility, ink quality, smudges, or fading are NOT "
    "meaningfully evaluable from OCR text alone — exclude them unless the "
    "page has structural evidence of damage."
)


def _build_prescreen_prompt(
    rules: list[AuditRule],
    extraction: dict,
    page_num: int,
    section_info: dict | None,
) -> str:
    """Build a compact prompt for the applicability pre-screen."""
    rule_lines = []
    for r in rules:
        summary = r.text[:100]
        rule_lines.append(f"  {r.id}: {summary}")
    rules_block = "\n".join(rule_lines)

    md = extraction.get("markdown", "")[:_PRESCREEN_MAX_CONTENT_CHARS]

    hw_count = extraction.get("handwritten_count", 0)
    sigs = extraction.get("signatures", [])
    kv_pairs = extraction.get("key_value_pairs", [])
    sel_marks = extraction.get("selection_marks", [])

    meta_parts = [
        f"Handwritten words: {hw_count}",
        f"Signatures: {len(sigs)}",
        f"Form fields: {len(kv_pairs)}",
        f"Checkboxes: {len(sel_marks)}",
    ]

    if kv_pairs:
        kv_sample = ", ".join(
            kv.get("key", "?")[:30] for kv in kv_pairs[:8]
        )
        meta_parts.append(f"Field labels: {kv_sample}")

    metadata = "; ".join(meta_parts)

    section_ctx = ""
    if section_info:
        sec_name = section_info.get("section_name", "")
        sec_type = section_info.get("section_type", "")
        if sec_name:
            section_ctx = f"\nDocument section: \"{sec_name}\" (type: {sec_type})"

    return (
        f"Page {page_num} of a pharmaceutical batch production record.{section_ctx}\n\n"
        f"PAGE METADATA: {metadata}\n\n"
        f"PAGE CONTENT (truncated):\n{md}\n\n"
        f"CANDIDATE RULES ({len(rules)} total):\n{rules_block}\n\n"
        f"Which of these rules are APPLICABLE to this page? A rule is applicable "
        f"if the page contains content that the rule can meaningfully evaluate "
        f"(e.g. a signature rule is applicable only if the page has sign-off "
        f"sections; a date rule is applicable only if dates are expected).\n\n"
        f"Return ONLY the IDs of applicable rules. If very few rules apply, "
        f"that is fine. Include a brief reasoning summary."
    )


class ApplicabilityGate:
    """Two-tier hybrid gate for rule applicability filtering.

    Tier 1 (static): ``cannot_evaluate`` rules are instantly skipped.
    Tier 2 (LLM pre-screen): A single lightweight LLM call per page.

    When mode is ``"static"``, falls back to the original 4-stage static
    filter chain (no LLM cost).
    """

    # ── Static mode (original 4-stage filter chain) ──────────

    def filter_rules(
        self,
        rules: list[AuditRule],
        page_type: str,
        section_info: dict | None,
        extraction: dict,
    ) -> tuple[list[AuditRule], list[RuleEvaluation]]:
        """Static filter chain (no LLM calls). Used when mode is ``"static"``.

        Filter order:
          1. Cannot-evaluate  (evaluation_mode == "cannot_evaluate")
          2. Page type mismatch
          3. Section type mismatch
          4. Keyword absence
        """
        applicable: list[AuditRule] = []
        skipped: list[RuleEvaluation] = []

        for rule in rules:
            reason = self._should_skip(rule, page_type, section_info, extraction)
            if reason is not None:
                skipped.append(RuleEvaluation(
                    rule_id=rule.id,
                    status="not_applicable",
                    confidence=1.0,
                    reasoning=reason,
                ))
            else:
                applicable.append(rule)

        if skipped:
            logger.debug(
                "ApplicabilityGate(static): %d/%d rules skipped (page_type=%s)",
                len(skipped), len(rules), page_type,
            )

        return applicable, skipped

    def _should_skip(
        self,
        rule: AuditRule,
        page_type: str,
        section_info: dict | None,
        extraction: dict,
    ) -> str | None:
        """Return a skip reason string, or None if the rule is applicable."""

        if rule.evaluation_mode == "cannot_evaluate":
            return rule.cannot_evaluate_reason or "Rule requires external data not available for evaluation"

        if rule.applicable_page_types and page_type not in rule.applicable_page_types:
            return (
                f"Page type '{page_type}' is not in applicable types "
                f"{rule.applicable_page_types} for this rule"
            )

        if rule.applicable_section_types and section_info:
            sec_type = section_info.get("section_type", "")
            if sec_type and sec_type not in rule.applicable_section_types:
                return (
                    f"Section type '{sec_type}' is not in applicable types "
                    f"{rule.applicable_section_types} for this rule"
                )

        if rule.keywords:
            md_lower = extraction.get("markdown", "").lower()
            if not any(kw.lower() in md_lower for kw in rule.keywords):
                return (
                    f"None of the required keywords {rule.keywords[:5]} "
                    f"found on this page"
                )

        return None

    # ── LLM pre-screen (Tier 2) ──────────────────────────────

    async def llm_prescreen(
        self,
        rules: list[AuditRule],
        extraction: dict,
        page_num: int,
        llm: LLMProvider,
        section_info: dict | None = None,
    ) -> set[str]:
        """Run a lightweight LLM call to determine which rules apply to a page.

        Returns the set of applicable rule IDs.
        """
        if not rules:
            return set()

        prompt = _build_prescreen_prompt(rules, extraction, page_num, section_info)

        try:
            result = await llm.generate_structured(
                prompt, ApplicabilityScreenResult, system=_PRESCREEN_SYSTEM,
            )
            if not isinstance(result, ApplicabilityScreenResult):
                result = ApplicabilityScreenResult.model_validate(result)

            valid_ids = {r.id for r in rules}
            applicable_ids = {rid for rid in result.applicable_rule_ids if rid in valid_ids}

            logger.debug(
                "LLM pre-screen page %d: %d/%d rules applicable",
                page_num, len(applicable_ids), len(rules),
            )
            return applicable_ids

        except Exception:
            logger.warning(
                "LLM pre-screen failed for page %d, falling back to all rules applicable",
                page_num, exc_info=True,
            )
            return {r.id for r in rules}

    # ── Hybrid mode (Tier 1 static + Tier 2 LLM) ────────────

    async def filter_rules_hybrid(
        self,
        rules: list[AuditRule],
        extraction: dict,
        page_num: int,
        llm: LLMProvider,
        section_info: dict | None = None,
        prescreen_cache: dict[int, set[str]] | None = None,
    ) -> tuple[list[AuditRule], list[RuleEvaluation]]:
        """Two-tier filtering: static gate for cannot-evaluate, then LLM pre-screen.

        Parameters
        ----------
        prescreen_cache:
            Optional dict mapping page_num -> set of applicable rule IDs from
            a previous LLM pre-screen call. If the page is already cached, the
            LLM call is skipped.
        """
        # Tier 1: static cannot-evaluate gate
        tier1_applicable: list[AuditRule] = []
        skipped: list[RuleEvaluation] = []

        for rule in rules:
            if rule.evaluation_mode == "cannot_evaluate":
                skipped.append(RuleEvaluation(
                    rule_id=rule.id,
                    status="not_applicable",
                    confidence=1.0,
                    reasoning=rule.cannot_evaluate_reason or "Rule requires external data not available for evaluation",
                ))
            else:
                tier1_applicable.append(rule)

        if not tier1_applicable:
            return [], skipped

        # Tier 2: LLM pre-screen (cached per page)
        if prescreen_cache is not None and page_num in prescreen_cache:
            applicable_ids = prescreen_cache[page_num]
        else:
            applicable_ids = await self.llm_prescreen(
                tier1_applicable, extraction, page_num, llm, section_info,
            )
            if prescreen_cache is not None:
                prescreen_cache[page_num] = applicable_ids

        applicable: list[AuditRule] = []
        for rule in tier1_applicable:
            if rule.id in applicable_ids:
                applicable.append(rule)
            else:
                skipped.append(RuleEvaluation(
                    rule_id=rule.id,
                    status="not_applicable",
                    confidence=0.9,
                    reasoning="LLM pre-screen determined this rule is not applicable to this page content",
                ))

        if skipped:
            logger.debug(
                "ApplicabilityGate(hybrid): %d/%d rules skipped on page %d",
                len(skipped), len(rules), page_num,
            )

        return applicable, skipped
