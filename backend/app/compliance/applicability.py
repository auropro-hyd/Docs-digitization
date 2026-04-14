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
from app.compliance.rules.profiles import (
    normalize_document_type,
    normalize_section_type,
)
from app.compliance.rules.registry import AuditRule
from app.core.ports.llm import LLMProvider

logger = logging.getLogger(__name__)

_PRESCREEN_MAX_CONTENT_CHARS = 3000

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
        summary = r.text[:200]
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
        document_type: str,
        page_type: str,
        section_info: dict | None,
        extraction: dict,
    ) -> tuple[list[AuditRule], list[RuleEvaluation], dict[str, list[str]]]:
        """Static filter chain (no LLM calls). Used when mode is ``"static"``.

        Filter order:
          1. Cannot-evaluate  (evaluation_mode == "cannot_evaluate")
          2. Document type mismatch
          3. Section type mismatch
          4. Page type mismatch
          5. Keyword absence
        """
        applicable: list[AuditRule] = []
        skipped: list[RuleEvaluation] = []
        traces: dict[str, list[str]] = {}

        section_type = self._normalized_section_type(section_info)
        for rule in rules:
            reason, trace = self._should_skip(
                rule,
                document_type=document_type,
                page_type=page_type,
                section_type=section_type,
                extraction=extraction,
                include_keyword_gate=True,
            )
            traces[rule.id] = trace
            if reason is not None:
                skipped.append(RuleEvaluation(
                    rule_id=rule.id,
                    status="not_applicable",
                    confidence=1.0,
                    reasoning=reason,
                    applicability_trace=trace,
                ))
            else:
                applicable.append(rule)

        if skipped:
            logger.debug(
                "ApplicabilityGate(static): %d/%d rules skipped (page_type=%s)",
                len(skipped), len(rules), page_type,
            )

        return applicable, skipped, traces

    def _should_skip(
        self,
        rule: AuditRule,
        document_type: str,
        page_type: str,
        section_type: str,
        extraction: dict,
        include_keyword_gate: bool,
    ) -> tuple[str | None, list[str]]:
        """Return (skip_reason | None, applicability_trace)."""

        normalized_doc_type = normalize_document_type(document_type)
        trace: list[str] = []

        if rule.evaluation_mode == "cannot_evaluate":
            reason = rule.cannot_evaluate_reason or "Rule requires external data not available for evaluation"
            trace.append(f"cannot_evaluate: fail ({reason})")
            return reason, trace
        trace.append("cannot_evaluate: pass")

        if rule.applicable_document_types:
            allowed_doc_types = {normalize_document_type(v) for v in rule.applicable_document_types}
            if normalized_doc_type not in allowed_doc_types:
                reason = (
                    f"Document type '{normalized_doc_type}' is not in applicable types "
                    f"{sorted(allowed_doc_types)} for this rule"
                )
                trace.append(f"document_type: fail ({reason})")
                return reason, trace
            trace.append(f"document_type: pass ({normalized_doc_type} in {sorted(allowed_doc_types)})")
        else:
            trace.append("document_type: pass (no include constraints)")

        if rule.excluded_document_types:
            excluded_doc_types = {normalize_document_type(v) for v in rule.excluded_document_types}
            if normalized_doc_type in excluded_doc_types:
                reason = f"Document type '{normalized_doc_type}' is excluded for this rule"
                trace.append(f"document_type_exclusion: fail ({reason})")
                return reason, trace
            trace.append("document_type_exclusion: pass")
        else:
            trace.append("document_type_exclusion: pass (no exclusions)")

        if rule.applicable_section_types and section_type:
            allowed_sections = {normalize_section_type(v) for v in rule.applicable_section_types}
            if section_type not in allowed_sections:
                reason = (
                    f"Section type '{section_type}' is not in applicable types "
                    f"{sorted(allowed_sections)} for this rule"
                )
                trace.append(f"section_type: fail ({reason})")
                return reason, trace
            trace.append(f"section_type: pass ({section_type} in {sorted(allowed_sections)})")
        elif rule.applicable_section_types and not section_type:
            trace.append("section_type: pass (section missing; deferred)")
        else:
            trace.append("section_type: pass (no section constraints)")

        if rule.applicable_page_types and page_type not in rule.applicable_page_types:
            reason = (
                f"Page type '{page_type}' is not in applicable types "
                f"{rule.applicable_page_types} for this rule"
            )
            trace.append(f"page_type: fail ({reason})")
            return reason, trace
        if rule.applicable_page_types:
            trace.append(f"page_type: pass ({page_type} in {rule.applicable_page_types})")
        else:
            trace.append("page_type: pass (no page constraints)")

        if include_keyword_gate and rule.keywords:
            md_lower = extraction.get("markdown", "").lower()
            # Also search KV keys/values and signature labels so keywords
            # match structured metadata, not just raw markdown.
            kv_text = " ".join(
                f"{kv.get('key', '')} {kv.get('value', '')}"
                for kv in extraction.get("key_value_pairs", [])
            ).lower()
            sig_text = " ".join(
                s.get("label", "") for s in extraction.get("signatures", [])
            ).lower()
            searchable = f"{md_lower} {kv_text} {sig_text}"
            if not any(kw.lower() in searchable for kw in rule.keywords):
                reason = (
                    f"None of the required keywords {rule.keywords[:5]} "
                    f"found on this page"
                )
                trace.append(f"keyword_gate: fail ({reason})")
                return reason, trace
            trace.append("keyword_gate: pass")
        elif include_keyword_gate:
            trace.append("keyword_gate: pass (no keyword constraints)")
        else:
            trace.append("keyword_gate: deferred_to_prescreen")

        return None, trace

    @staticmethod
    def _normalized_section_type(section_info: dict | None) -> str:
        if not section_info:
            return ""
        return normalize_section_type(str(section_info.get("section_type", "")))

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
        document_type: str,
        page_type: str,
        extraction: dict,
        page_num: int,
        llm: LLMProvider,
        section_info: dict | None = None,
        prescreen_cache: dict[int, set[str]] | None = None,
    ) -> tuple[list[AuditRule], list[RuleEvaluation], dict[str, list[str]]]:
        """Two-tier filtering: static gate for cannot-evaluate, then LLM pre-screen.

        Parameters
        ----------
        prescreen_cache:
            Optional dict mapping page_num -> set of applicable rule IDs from
            a previous LLM pre-screen call. If the page is already cached, the
            LLM call is skipped.
        """
        # Tier 1: deterministic static gate (cannot_evaluate -> doc_type -> section -> page)
        tier1_applicable: list[AuditRule] = []
        skipped: list[RuleEvaluation] = []
        traces: dict[str, list[str]] = {}
        section_type = self._normalized_section_type(section_info)

        for rule in rules:
            reason, trace = self._should_skip(
                rule,
                document_type=document_type,
                page_type=page_type,
                section_type=section_type,
                extraction=extraction,
                include_keyword_gate=False,
            )
            traces[rule.id] = trace
            if reason is not None:
                skipped.append(RuleEvaluation(
                    rule_id=rule.id,
                    status="not_applicable",
                    confidence=1.0,
                    reasoning=reason,
                    applicability_trace=trace,
                ))
            else:
                tier1_applicable.append(rule)

        if not tier1_applicable:
            return [], skipped, traces

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
                traces.setdefault(rule.id, []).append("llm_prescreen: pass")
                applicable.append(rule)
            else:
                rule_trace = traces.setdefault(rule.id, [])
                rule_trace.append("llm_prescreen: fail")
                skipped.append(RuleEvaluation(
                    rule_id=rule.id,
                    status="not_applicable",
                    confidence=0.9,
                    reasoning="LLM pre-screen determined this rule is not applicable to this page content",
                    applicability_trace=rule_trace,
                ))

        if skipped:
            logger.debug(
                "ApplicabilityGate(hybrid): %d/%d rules skipped on page %d",
                len(skipped), len(rules), page_num,
            )

        return applicable, skipped, traces
