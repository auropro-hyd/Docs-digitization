"""Rule-batch evaluator: shared engine for all compliance agents.

Each call evaluates a small batch of rules against a single page of content
using ``generate_structured()`` with the ``RuleBatchResult`` schema.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from app.compliance.applicability import ApplicabilityGate
from app.compliance.context_builder import build_enriched_context, classify_page_type
from app.compliance.models import (
    AGENT_DISPLAY_NAMES,
    SEVERITY_WEIGHTS,
    AgentReport,
    CategoryScore,
    ComplianceFinding,
    RuleBatchResult,
    RuleEvaluation,
    RuleResult,
)
from app.compliance.rules.registry import AuditRule, RuleBatch
from app.config.settings import get_settings
from app.core.ports.llm import LLMProvider
from app.core.ports.vlm import VLMProvider

logger = logging.getLogger(__name__)

_STATUS_SEVERITY = {
    "non_compliant": 4,
    "uncertain": 3,
    "error": 2,
    "compliant": 1,
    "not_applicable": 0,
}

_VALID_STATUSES = {"compliant", "non_compliant", "not_applicable", "uncertain", "error"}

_SIGNATURE_KEYWORDS = frozenset({
    "signature", "initial", "initials", "initialed", "initialled",
    "sign", "signed", "countersign", "countersigned",
})


def _is_signature_rule(rule: AuditRule) -> bool:
    text_lower = rule.text.lower()
    return any(kw in text_lower for kw in _SIGNATURE_KEYWORDS)

_OCR_AWARENESS = (
    "\n\nCRITICAL — OCR ARTIFACT AWARENESS:\n"
    "The content you evaluate is extracted via OCR from scanned documents. "
    "You MUST distinguish between genuine compliance issues and OCR artifacts:\n"
    "1. HANDWRITTEN TEXT: Handwritten entries (signatures, initials, operator "
    "names, dates) are frequently garbled by OCR. Text like 'Noga', 'staten', "
    "'3cleader', 'NOOR', 'N088', 'K 01/10/2025' in 'Done by'/'Checked by' "
    "columns are VALID operator identifications — they are OCR's best attempt "
    "at reading handwriting. Treat ANY text in a signature/identity column as "
    "a valid entry unless the cell is genuinely empty or blank.\n"
    "2. DATE MISREADS: OCR commonly misreads handwritten years — '2015' is "
    "almost certainly '2025', '205' is a truncated '2025', '20#' is a garbled "
    "'2025'. These are OCR errors, NOT data integrity issues. Do NOT flag "
    "them as unrealistic dates or transcription errors.\n"
    "3. DASHES ARE VALID: A dash (-), series of dashes (----), or em-dash (—) "
    "in a form field means 'not applicable' or 'not performed'. This is a "
    "legitimate annotation, NOT a blank/empty field. Never flag dashed fields "
    "as missing data.\n"
    "4. BPCR WORKFLOW: Batch Production and Control Record templates are "
    "printed BEFORE manufacturing begins. Activity dates (Sep/Oct 2025) being "
    "after the template print date (e.g., 18-Sep-2025) is COMPLETELY NORMAL. "
    "This is NOT post-dating.\n"
    "5. LEGIBILITY vs OCR QUALITY: You cannot assess whether the original "
    "document has smudges, fading, or unapproved inks from OCR text alone. "
    "Garbled OCR output does NOT mean the original document is illegible — "
    "it means the OCR engine struggled with handwriting. Only flag legibility "
    "issues when there is structural evidence (e.g., missing entire sections, "
    "page appears blank, critical data fields completely absent).\n"
    "6. SIGNATURE DETECTION: If the STRUCTURED METADATA shows a signature "
    "detected with status='signed', trust it — even if the markdown text "
    "looks garbled. The document intelligence engine uses visual analysis "
    "for signature detection, which is more reliable than OCR text."
)

_SYSTEM_PROMPTS: dict[str, str] = {
    "alcoa": (
        "You are an ALCOA++ compliance reviewer for pharmaceutical batch production records. "
        "You evaluate data-integrity rules against OCR-extracted markdown content. "
        "Base decisions strictly on explicit evidence visible in the content. "
        "If a rule cannot be evaluated from the given page, mark it not_applicable."
        + _OCR_AWARENESS
    ),
    "gmp": (
        "You are a GMP (Good Manufacturing Practice) compliance reviewer. "
        "You evaluate GMP documentation rules against pharmaceutical manufacturing records. "
        "Base decisions strictly on explicit evidence visible in the content."
        + _OCR_AWARENESS
    ),
    "checklist": (
        "You are a checklist verification specialist for pharmaceutical documents. "
        "You verify completeness of checklists, signatures, dates, and required fields. "
        "Base decisions strictly on explicit evidence visible in the content."
        + _OCR_AWARENESS
    ),
    "sop": (
        "You are an SOP compliance reviewer for pharmaceutical manufacturing. "
        "You verify that manufacturing steps align with Standard Operating Procedures. "
        "Base decisions strictly on explicit evidence visible in the content."
        + _OCR_AWARENESS
    ),
}


def _format_rule_for_prompt(rule: AuditRule) -> str:
    """Format a single rule with its pass criteria and skip conditions."""
    lines = [f"- [{rule.id}] {rule.text}"]
    if rule.pass_criteria:
        lines.append(f"  PASS CRITERIA: {rule.pass_criteria}")
    if rule.skip_conditions:
        for cond in rule.skip_conditions:
            lines.append(f"  SKIP IF: {cond}")
    return "\n".join(lines)


def _build_batch_prompt(
    rules: list[AuditRule],
    enriched_content: str,
    page_num: int,
    section_info: dict | None = None,
) -> str:
    rules_section = "\n".join(_format_rule_for_prompt(r) for r in rules)

    context_header = ""
    if section_info:
        sec_name = section_info.get("section_name", "Unknown")
        sec_type = section_info.get("section_type", "unknown")
        start = section_info.get("start_page", "?")
        end = section_info.get("end_page", "?")
        try:
            page_pos = page_num - int(start) + 1
            total_in_sec = int(end) - int(start) + 1
        except (ValueError, TypeError):
            page_pos = page_num
            total_in_sec = "?"
        context_header = (
            f"DOCUMENT CONTEXT:\n"
            f'This page belongs to section: "{sec_name}" '
            f"(type: {sec_type}, pages {start}-{end}).\n"
            f"This is page {page_pos} of {total_in_sec} within this section.\n\n"
        )

    return (
        f"{context_header}"
        f"Evaluate ONLY the following {len(rules)} rules against this page.\n\n"
        f"You are given BOTH the OCR markdown AND structured metadata extracted by "
        f"document intelligence. USE BOTH to make your assessment:\n"
        f"- The STRUCTURED METADATA section contains machine-detected signatures, "
        f"handwriting indicators, form field key-value pairs, and checkbox states.\n"
        f"- If metadata says 'Signatures detected: 0', do NOT flag missing signatures "
        f"unless the page content clearly requires them (e.g., a sign-off section).\n"
        f"- If metadata says 'Handwritten regions: 0', the page is likely printed/typed.\n"
        f"- Use key-value pairs to verify form fields (e.g., 'Done By', 'Date', 'Checked By').\n"
        f"- Empty key-value fields (value is [empty/blank]) indicate missing entries.\n"
        f"- Fields containing dashes (-), (----), or (—) are NOT empty — they mean "
        f"'not applicable' or 'not performed' and are valid annotations.\n"
        f"- Garbled text in 'Done by'/'Checked by' columns (e.g., 'Noga', 'staten', "
        f"'N088') is OCR's reading of handwritten signatures — treat as VALID.\n\n"
        f"IMPORTANT RULE GUIDANCE:\n"
        f"- Each rule below may include PASS CRITERIA and SKIP IF conditions.\n"
        f"- PASS CRITERIA tells you exactly what constitutes compliance — follow it strictly.\n"
        f"- SKIP IF conditions tell you when to mark the rule not_applicable — check these first.\n"
        f"- If no PASS CRITERIA is given, use your expert judgment based on the rule text.\n\n"
        f"For each rule, return a JSON object with:\n"
        f'  rule_id, status ("compliant"|"non_compliant"|"not_applicable"|"uncertain"),\n'
        f"  confidence (float 0.0-1.0),\n"
        f"  severity (only if non_compliant: \"critical\"|\"major\"|\"minor\"|\"observation\"),\n"
        f"  reasoning (REQUIRED for ALL statuses: 1-3 sentences explaining WHY this "
        f"status was chosen. MUST reference at least ONE specific data point from the "
        f"page — a field name, value, signature label, table cell, or section heading. "
        f"Do NOT use vague statements like 'data appears compliant'. "
        f"For compliant: cite what you found that satisfies the rule, e.g. "
        f"'Done By field contains signature \"S. Patel\" with date 15/03/2025'. "
        f"For non_compliant: cite what is missing or incorrect, e.g. "
        f"'Checked By field is blank — no countersignature present'),\n"
        f"  evidence (REQUIRED for ALL statuses: a VERBATIM excerpt (exact text or "
        f"metadata value) from the page that supports your assessment. For compliant "
        f"rules, quote the specific field or text that proves compliance. "
        f"Example: 'Done by: S. Patel | Date: 15/03/2025 | Checked by: R. Kumar'),\n"
        f"  description (what the issue is — empty only if compliant),\n"
        f"  recommendation (remediation guidance — empty only if compliant).\n\n"
        f"IMPORTANT: If a rule is about signatures/initials/dates but the page has no "
        f"sign-off sections or form fields requiring them, mark it not_applicable — "
        f"not every page needs signatures.\n\n"
        f"Confidence guidelines:\n"
        f"  1.0 = Absolutely certain based on clear evidence\n"
        f"  0.8-0.99 = High confidence with strong evidence\n"
        f"  0.6-0.79 = Moderate confidence, some ambiguity\n"
        f"  0.4-0.59 = Low confidence, significant ambiguity\n"
        f"  <0.4 = Very uncertain, insufficient evidence\n\n"
        f"Additionally, note any cross-page references (material names, equipment IDs, "
        f"batch numbers, values to verify against other sections) as cross_references.\n\n"
        f"RULES TO EVALUATE:\n{rules_section}\n\n"
        f"{enriched_content}"
    )


class RuleBatchEvaluator:
    """Evaluates a batch of rules against one page with retry on failure."""

    _MAX_RETRIES = 3

    async def evaluate_batch(
        self,
        batch: RuleBatch,
        page_content: str,
        page_num: int,
        llm: LLMProvider,
        section_info: dict | None = None,
    ) -> tuple[str, int, RuleBatchResult]:
        """Returns (batch_id, page_num, result)."""
        if not page_content.strip():
            evals = [
                RuleEvaluation(rule_id=r.id, status="not_applicable")
                for r in batch.rules
            ]
            return batch.batch_id, page_num, RuleBatchResult(evaluations=evals)

        system = _SYSTEM_PROMPTS.get(batch.agent, _SYSTEM_PROMPTS["alcoa"])
        last_error: Exception | None = None

        for attempt in range(1 + self._MAX_RETRIES):
            rules_to_eval = batch.rules
            if attempt > 0 and len(batch.rules) > 3:
                rules_to_eval = batch.rules[:3]
                logger.info(
                    "Retry %d for batch %s page %d with reduced rules (%d→%d)",
                    attempt, batch.batch_id, page_num, len(batch.rules), len(rules_to_eval),
                )

            prompt = _build_batch_prompt(rules_to_eval, page_content, page_num, section_info)

            try:
                result = await llm.generate_structured(prompt, RuleBatchResult, system=system)
                if not isinstance(result, RuleBatchResult):
                    result = RuleBatchResult.model_validate(result)

                if attempt > 0 and len(rules_to_eval) < len(batch.rules):
                    evaluated_ids = {ev.rule_id for ev in result.evaluations}
                    for r in batch.rules:
                        if r.id not in evaluated_ids:
                            result.evaluations.append(RuleEvaluation(
                                rule_id=r.id, status="error",
                                description="Skipped during retry (batch reduced)",
                            ))

                return batch.batch_id, page_num, result
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Batch %s page %d attempt %d failed: %s",
                    batch.batch_id, page_num, attempt + 1, exc,
                )

        logger.error("Batch %s page %d exhausted retries", batch.batch_id, page_num)
        evals = [
            RuleEvaluation(rule_id=r.id, status="error", description="Evaluation failed after retry")
            for r in batch.rules
        ]
        return batch.batch_id, page_num, RuleBatchResult(evaluations=evals)


async def run_agent_evaluation(
    agent: str,
    batches: list[RuleBatch],
    extractions: list[dict],
    llm: LLMProvider,
    document_type: str = "batch_record",
    max_concurrent: int = 10,
    progress_callback=None,
    prescreen_callback=None,
    section_map: dict[int, dict] | None = None,
    global_kv_pairs: list[dict] | None = None,
    vlm: VLMProvider | None = None,
    doc_id: str | None = None,
) -> list[tuple[str, int, RuleBatchResult]]:
    """Fan-out all (batch x page) combos for one agent, with concurrency limit.

    Supports two applicability modes (via ``ComplianceConfig.applicability_mode``):

    ``"static"``: Original 4-stage static filter chain per batch (no LLM cost).
    ``"llm"``:    Tier 1 static (cannot_evaluate) + Tier 2 LLM pre-screen
                  per page. The pre-screen runs once per page with ALL agent
                  rules, then cached results are used for per-batch filtering.

    Parameters
    ----------
    prescreen_callback:
        Optional async callable ``(pages_done, total_pages, stats_dict)``
        invoked during LLM pre-screen to report progress.
    """
    settings = get_settings()
    mode = settings.compliance.applicability_mode

    evaluator = RuleBatchEvaluator()
    gate = ApplicabilityGate()
    semaphore = asyncio.Semaphore(max_concurrent)
    total_tasks = len(batches) * len(extractions)
    completed = 0

    # Diagnostic: VC-* prompts defined in vision_evaluator with NO
    # rule reference will never fire. Surface this once per agent so
    # an extended prompt (like VC-DOC-QUALITY's scan-defect coverage
    # in PR #43) doesn't sit unused because no rule was updated to
    # tag it. Auto-no-op when no telemetry sink is bound.
    try:
        from app.compliance.vision_evaluator import audit_unused_vc_prompts
        unique_rules: list[AuditRule] = []
        seen: set[str] = set()
        for batch in batches:
            for rule in batch.rules:
                if rule.id not in seen:
                    seen.add(rule.id)
                    unique_rules.append(rule)
        audit_unused_vc_prompts(unique_rules, agent=agent)
    except Exception:  # pragma: no cover — defensive
        logger.debug("VC prompt coverage audit failed", exc_info=True)

    page_type_cache: dict[int, str] = {}

    # ── LLM mode: pre-screen all rules per page before batch evaluation ──
    prescreen_cache: dict[int, set[str]] = {}

    if mode == "llm" and batches:
        all_agent_rules: list[AuditRule] = []
        seen_ids: set[str] = set()
        for batch in batches:
            for rule in batch.rules:
                if rule.id not in seen_ids:
                    if rule.evaluation_mode != "cannot_evaluate":
                        all_agent_rules.append(rule)
                    seen_ids.add(rule.id)

        if all_agent_rules:
            total_pages = len(extractions)
            total_rule_count = len(all_agent_rules)
            pages_done = 0
            prescreen_lock = asyncio.Lock()

            if prescreen_callback:
                await prescreen_callback(0, total_pages, {
                    "total_rules": total_rule_count,
                    "status": "started",
                })

            async def _prescreen_page(ext: dict) -> None:
                nonlocal pages_done
                page_num = ext.get("page_num", 0)
                sec_info = section_map.get(page_num) if section_map else None
                page_type = classify_page_type(ext)
                try:
                    candidate_rules, _, _ = await gate.filter_rules_hybrid(
                        all_agent_rules,
                        document_type=document_type,
                        page_type=page_type,
                        extraction=ext,
                        page_num=page_num,
                        llm=llm,
                        section_info=sec_info,
                        prescreen_cache=None,
                    )
                    applicable_ids = {r.id for r in candidate_rules}
                    prescreen_cache[page_num] = applicable_ids
                except Exception:
                    logger.warning(
                        "Pre-screen failed for page %d, allowing all rules",
                        ext.get("page_num", 0), exc_info=True,
                    )
                    prescreen_cache[page_num] = {r.id for r in all_agent_rules}

                async with prescreen_lock:
                    pages_done += 1
                    if prescreen_callback:
                        applicable_count = len(prescreen_cache.get(page_num, set()))
                        await prescreen_callback(pages_done, total_pages, {
                            "total_rules": total_rule_count,
                            "page_num": page_num,
                            "applicable_count": applicable_count,
                            "status": "screening",
                        })

            pre_results = await asyncio.gather(
                *[_prescreen_page(ext) for ext in extractions],
                return_exceptions=True,
            )
            for i, r in enumerate(pre_results):
                if isinstance(r, Exception):
                    pn = extractions[i].get("page_num", 0)
                    logger.warning("Pre-screen gather error page %d: %s", pn, r)
                    prescreen_cache[pn] = {rule.id for rule in all_agent_rules}

            avg_applicable = (
                sum(len(ids) for ids in prescreen_cache.values()) / max(len(prescreen_cache), 1)
            )

            logger.info(
                "LLM pre-screen complete for %s: %d pages screened, avg %.0f/%d rules applicable",
                agent, len(prescreen_cache), avg_applicable, total_rule_count,
            )

            if prescreen_callback:
                await prescreen_callback(total_pages, total_pages, {
                    "total_rules": total_rule_count,
                    "avg_applicable": round(avg_applicable),
                    "status": "complete",
                })

    # ── Vision evaluation setup ─────────────────────────────────
    compliance_settings = settings.compliance
    vlm_available = (
        vlm is not None
        and compliance_settings.vlm_evaluation_enabled
        and settings.vlm.enabled
    )

    vision_evaluator = None
    if vlm_available:
        from app.compliance.vision_evaluator import VisionBatchEvaluator
        vision_evaluator = VisionBatchEvaluator()
        logger.info("VLM enabled for agent %s — vision rules will be evaluated", agent)

    # ── Per-batch evaluation ─────────────────────────────────────

    async def _run(batch: RuleBatch, ext: dict) -> tuple[str, int, RuleBatchResult]:
        nonlocal completed
        page_num = ext.get("page_num", 0)
        sec_info = section_map.get(page_num) if section_map else None

        if mode == "llm":
            if page_num not in page_type_cache:
                page_type_cache[page_num] = classify_page_type(ext)
            page_type = page_type_cache[page_num]
            applicable_rules, gate_evals, gate_trace_map = await gate.filter_rules_hybrid(
                batch.rules,
                document_type=document_type,
                page_type=page_type,
                extraction=ext,
                page_num=page_num,
                llm=llm,
                section_info=sec_info,
                prescreen_cache=prescreen_cache,
            )
        else:
            if page_num not in page_type_cache:
                page_type_cache[page_num] = classify_page_type(ext)
            page_type = page_type_cache[page_num]
            applicable_rules, gate_evals, gate_trace_map = gate.filter_rules(
                batch.rules, document_type, page_type, sec_info, ext,
            )

        if not applicable_rules:
            completed += 1
            result = (batch.batch_id, page_num, RuleBatchResult(evaluations=gate_evals))
            if progress_callback:
                await progress_callback(completed, total_tasks, batch, result)
            return result

        # Split rules by evaluation strategy
        text_rules: list[AuditRule] = []
        vision_only_rules: list[AuditRule] = []
        text_and_vision_rules: list[AuditRule] = []
        text_primary_rules: list[AuditRule] = []
        llm_arbitrated_rules: list[AuditRule] = []

        for rule in applicable_rules:
            strategy = rule.evaluation_strategy
            if strategy == "vision" and vlm_available:
                vision_only_rules.append(rule)
            elif strategy == "text_and_vision" and vlm_available:
                text_and_vision_rules.append(rule)
                text_rules.append(rule)
            elif strategy == "text_primary" and vlm_available:
                text_primary_rules.append(rule)
                text_rules.append(rule)
            elif strategy == "llm_arbitrated" and vlm_available:
                llm_arbitrated_rules.append(rule)
                text_rules.append(rule)
            elif strategy == "vision" and not vlm_available:
                if compliance_settings.vlm_fallback_to_text:
                    text_rules.append(rule)
                else:
                    gate_evals.append(RuleEvaluation(
                        rule_id=rule.id,
                        status="not_applicable",
                        confidence=1.0,
                        reasoning="VLM unavailable — vision-only rule skipped",
                        applicability_trace=["vlm_unavailable"],
                    ))
            elif strategy in ("text_primary", "llm_arbitrated") and not vlm_available:
                # VLM unavailable — run text only; no merge needed
                text_rules.append(rule)
            else:
                # Unknown / empty strategy — fall through to text but
                # flag the dangling value. This is the exact failure
                # mode that hid GMP-rule-11's missing llm_arbitrated
                # merge for weeks (rule declared the strategy, but no
                # evaluator branch existed → silent text-only). With
                # this warning the next dangling enum value surfaces
                # on the first run.
                if strategy and strategy not in ("text", "agentic_audit"):
                    logger.warning(
                        "compliance.unknown_evaluation_strategy — "
                        "rule %s declared evaluation_strategy=%r which "
                        "has no router branch; falling through to plain "
                        "text evaluation. Likely a typo or a strategy "
                        "added to YAML without an evaluator merge function.",
                        rule.id, strategy,
                    )
                    try:
                        from app.observability.run_telemetry import record_event  # noqa: PLC0415
                        record_event(
                            "compliance.unknown_evaluation_strategy",
                            level="warning",
                            rule_id=rule.id,
                            strategy=strategy,
                        )
                    except Exception:  # pragma: no cover — never break eval
                        pass
                text_rules.append(rule)

        all_vision_rules = vision_only_rules + text_and_vision_rules + text_primary_rules + llm_arbitrated_rules

        enriched = build_enriched_context(ext, page_num, global_kv_pairs=global_kv_pairs)

        # Run text and vision evaluations in parallel
        text_coro = None
        vision_coro = None

        if text_rules:
            text_batch = RuleBatch(
                batch_id=batch.batch_id,
                category=batch.category,
                agent=batch.agent,
                rules=text_rules,
            )
            text_coro = evaluator.evaluate_batch(
                text_batch, enriched, page_num, llm,
                section_info=sec_info,
            )

        if all_vision_rules and vision_evaluator and vlm:
            page_image = await _load_page_image(doc_id, page_num)
            if page_image:
                vision_batch = RuleBatch(
                    batch_id=f"{batch.batch_id}-vision",
                    category=batch.category,
                    agent=batch.agent,
                    rules=all_vision_rules,
                )
                raw_vision_coro = vision_evaluator.evaluate_batch(
                    vision_batch, page_image, page_num, vlm,
                )
                vision_coro = asyncio.wait_for(
                    raw_vision_coro, timeout=compliance_settings.vlm_timeout,
                )
            else:
                for rule in vision_only_rules:
                    gate_evals.append(RuleEvaluation(
                        rule_id=rule.id,
                        status="not_applicable",
                        reasoning="Page image unavailable for visual inspection",
                        applicability_trace=["image_unavailable"],
                    ))

        async with semaphore:
            coros = [c for c in (text_coro, vision_coro) if c is not None]
            if not coros:
                completed += 1
                result = (batch.batch_id, page_num, RuleBatchResult(evaluations=gate_evals))
                if progress_callback:
                    await progress_callback(completed, total_tasks, batch, result)
                return result

            gathered = await asyncio.gather(*coros, return_exceptions=True)

            all_evals: list[RuleEvaluation] = list(gate_evals)
            all_cross_refs = []
            text_eval_map: dict[str, RuleEvaluation] = {}
            vision_eval_map: dict[str, RuleEvaluation] = {}

            idx = 0
            if text_coro is not None:
                text_result = gathered[idx]
                idx += 1
                if isinstance(text_result, Exception):
                    logger.error("Text batch %s page %d failed: %s", batch.batch_id, page_num, text_result)
                    for r in text_rules:
                        all_evals.append(RuleEvaluation(
                            rule_id=r.id, status="error",
                            description=f"Text evaluation failed: {text_result}",
                        ))
                else:
                    _, _, llm_result = text_result
                    for ev in llm_result.evaluations:
                        ev.applicability_trace = gate_trace_map.get(ev.rule_id, [])
                        text_eval_map[ev.rule_id] = ev
                    all_cross_refs.extend(llm_result.cross_references)

            if vision_coro is not None:
                vision_result = gathered[idx]
                if isinstance(vision_result, Exception):
                    logger.error("Vision batch %s page %d failed: %s", batch.batch_id, page_num, vision_result)
                    for r in vision_only_rules:
                        all_evals.append(RuleEvaluation(
                            rule_id=r.id, status="error",
                            description=f"Vision evaluation failed: {vision_result}",
                        ))
                else:
                    _, _, vlm_result = vision_result
                    for ev in vlm_result.evaluations:
                        vision_eval_map[ev.rule_id] = ev

            # Merge results: vision-only, text-only, and dual-channel strategies
            merged_rule_ids: set[str] = set()

            for rule in vision_only_rules:
                ev = vision_eval_map.get(rule.id)
                if ev:
                    merged_rule_ids.add(rule.id)
                    all_evals.append(ev)

            for rule in text_and_vision_rules:
                merged_rule_ids.add(rule.id)
                text_ev = text_eval_map.get(rule.id)
                vision_ev = vision_eval_map.get(rule.id)
                all_evals.append(_merge_text_vision(rule, text_ev, vision_ev))

            for rule in text_primary_rules:
                merged_rule_ids.add(rule.id)
                text_ev = text_eval_map.get(rule.id)
                vision_ev = vision_eval_map.get(rule.id)
                all_evals.append(_merge_text_primary(rule, text_ev, vision_ev))

            for rule in llm_arbitrated_rules:
                merged_rule_ids.add(rule.id)
                text_ev = text_eval_map.get(rule.id)
                vision_ev = vision_eval_map.get(rule.id)
                ocr_text = ext.get("markdown", "")
                arb_result = await _merge_llm_arbitrated(rule, text_ev, vision_ev, llm, ocr_text)
                all_evals.append(arb_result)

            for rule_id, ev in text_eval_map.items():
                if rule_id not in merged_rule_ids:
                    all_evals.append(ev)

            merged_result = RuleBatchResult(
                evaluations=all_evals,
                cross_references=all_cross_refs,
            )
            completed += 1
            res = (batch.batch_id, page_num, merged_result)
            if progress_callback:
                await progress_callback(completed, total_tasks, batch, res)
            return res

    tasks = [_run(batch, ext) for batch in batches for ext in extractions]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    valid: list[tuple[str, int, RuleBatchResult]] = []
    for r in results:
        if isinstance(r, Exception):
            logger.error("Batch evaluation failed: %s", r)
        else:
            valid.append(r)

    return valid


async def _load_page_image(doc_id: str | None, page_num: int) -> bytes | None:
    """Load a page image for VLM evaluation, returning None on failure."""
    if not doc_id:
        return None
    try:
        from app.compliance.page_image_loader import load_page_image
        return await load_page_image(doc_id, page_num)
    except Exception:
        logger.warning("Failed to load page image for doc %s page %d", doc_id, page_num, exc_info=True)
        return None


def _merge_text_vision(
    rule: AuditRule,
    text_ev: RuleEvaluation | None,
    vision_ev: RuleEvaluation | None,
) -> RuleEvaluation:
    """Merge text and vision evaluations for a text_and_vision rule.

    Vision takes precedence for visual aspects (strikethrough, ink color, etc.)
    while text evaluation provides context from OCR content.
    """
    if text_ev is None and vision_ev is None:
        return RuleEvaluation(rule_id=rule.id, status="error", description="Both evaluations missing")
    if text_ev is None:
        return vision_ev  # type: ignore[return-value]
    if vision_ev is None:
        return text_ev

    # For visual aspects, vision result takes precedence
    text_sev = _STATUS_SEVERITY.get(text_ev.status, 0)
    vision_sev = _STATUS_SEVERITY.get(vision_ev.status, 0)

    if vision_sev >= text_sev:
        merged = RuleEvaluation(
            rule_id=rule.id,
            status=vision_ev.status,
            severity=vision_ev.severity or text_ev.severity,
            confidence=min(text_ev.confidence, vision_ev.confidence),
            reasoning=f"[Vision] {vision_ev.reasoning} [Text] {text_ev.reasoning}",
            evidence=f"[Vision] {vision_ev.evidence} [Text] {text_ev.evidence}",
            description=vision_ev.description or text_ev.description,
            recommendation=vision_ev.recommendation or text_ev.recommendation,
            applicability_trace=list(text_ev.applicability_trace),
        )
    else:
        merged = RuleEvaluation(
            rule_id=rule.id,
            status=text_ev.status,
            severity=text_ev.severity or vision_ev.severity,
            confidence=min(text_ev.confidence, vision_ev.confidence),
            reasoning=f"[Text] {text_ev.reasoning} [Vision] {vision_ev.reasoning}",
            evidence=f"[Text] {text_ev.evidence} [Vision] {vision_ev.evidence}",
            description=text_ev.description or vision_ev.description,
            recommendation=text_ev.recommendation or vision_ev.recommendation,
            applicability_trace=list(text_ev.applicability_trace),
        )

    return merged


def _merge_text_primary(
    rule: AuditRule,
    text_ev: RuleEvaluation | None,
    vision_ev: RuleEvaluation | None,
) -> RuleEvaluation:
    """Merge for text_primary strategy: text verdict wins unless vision escalates.

    Vision can only raise the severity (make things worse), never lower it.
    Use this for rules where OCR content is the authoritative source and vision
    supplements only to catch what text missed.
    """
    if text_ev is None and vision_ev is None:
        return RuleEvaluation(rule_id=rule.id, status="error", description="Both evaluations missing")
    if text_ev is None:
        return vision_ev  # type: ignore[return-value]
    if vision_ev is None:
        return text_ev

    text_sev = _STATUS_SEVERITY.get(text_ev.status, 0)
    vision_sev = _STATUS_SEVERITY.get(vision_ev.status, 0)

    if vision_sev > text_sev:
        # Vision escalates — use vision verdict
        return RuleEvaluation(
            rule_id=rule.id,
            status=vision_ev.status,
            severity=vision_ev.severity or text_ev.severity,
            confidence=min(text_ev.confidence, vision_ev.confidence),
            reasoning=f"[Vision] {vision_ev.reasoning} [Text] {text_ev.reasoning}",
            evidence=f"[Vision] {vision_ev.evidence} [Text] {text_ev.evidence}",
            description=vision_ev.description or text_ev.description,
            recommendation=vision_ev.recommendation or text_ev.recommendation,
            applicability_trace=list(text_ev.applicability_trace),
        )
    else:
        # Text wins (including ties)
        return RuleEvaluation(
            rule_id=rule.id,
            status=text_ev.status,
            severity=text_ev.severity or vision_ev.severity,
            confidence=min(text_ev.confidence, vision_ev.confidence),
            reasoning=f"[Text] {text_ev.reasoning} [Vision] {vision_ev.reasoning}",
            evidence=f"[Text] {text_ev.evidence} [Vision] {vision_ev.evidence}",
            description=text_ev.description or vision_ev.description,
            recommendation=text_ev.recommendation or vision_ev.recommendation,
            applicability_trace=list(text_ev.applicability_trace),
        )


async def _call_arbitrator(
    rule: AuditRule,
    text_ev: RuleEvaluation,
    vision_ev: RuleEvaluation,
    llm: LLMProvider,
    ocr_text: str,
) -> RuleEvaluation:
    """Ask the LLM to resolve a text-vs-vision conflict for a single rule.

    Called only when text_ev.status != vision_ev.status. Returns a new
    RuleEvaluation whose status and reasoning come from the LLM verdict.
    Raises on LLM failure so the caller can apply its fallback.
    """
    truncated_ocr = ocr_text[:3000]
    prompt = (
        f"You are resolving a conflict between two compliance evaluators for Rule {rule.number}.\n\n"
        f"RULE TEXT: {rule.text}\n"
        f"PASS CRITERIA: {rule.pass_criteria or '(none specified)'}\n\n"
        f"OCR CONTENT (may be truncated to 3000 chars):\n{truncated_ocr}\n\n"
        f"TEXT EVALUATOR VERDICT: {text_ev.status}\n"
        f"TEXT EVALUATOR REASONING: {text_ev.reasoning}\n\n"
        f"VISION EVALUATOR VERDICT: {vision_ev.status}\n"
        f"VISION EVALUATOR REASONING: {vision_ev.reasoning}\n\n"
        f"GUIDANCE: OCR often garbles or misses handwritten entries. When the vision evaluator "
        f"confirms that a handwritten entry is present, prefer the vision verdict over the text "
        f"evaluator's complaint about missing or garbled content.\n\n"
        f"Return ONLY a JSON object with these fields:\n"
        f'  "status": one of "compliant", "non_compliant", "not_applicable", "uncertain", "error"\n'
        f'  "confidence": float between 0.0 and 1.0\n'
        f'  "reasoning": string explaining your decision\n'
        f'  "evidence": string referencing specific content\n'
    )

    raw = await llm.generate(prompt)

    # Parse JSON from the response (handle markdown code fences if present)
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    data = json.loads(text)
    status = data.get("status", "uncertain")
    if status not in _VALID_STATUSES:
        status = "uncertain"

    return RuleEvaluation(
        rule_id=rule.id,
        status=status,
        confidence=float(data.get("confidence", 0.5)),
        reasoning=str(data.get("reasoning", "")),
        evidence=str(data.get("evidence", "")),
    )


async def _merge_llm_arbitrated(
    rule: AuditRule,
    text_ev: RuleEvaluation | None,
    vision_ev: RuleEvaluation | None,
    llm: LLMProvider | None,
    ocr_text: str,
) -> RuleEvaluation:
    """Merge for llm_arbitrated strategy.

    When text and vision agree (same status), return immediately — no LLM call.
    When they conflict, call _call_arbitrator. Falls back to higher-severity
    result if arbitration fails or llm is None.
    """
    if text_ev is None and vision_ev is None:
        return RuleEvaluation(rule_id=rule.id, status="error", description="Both evaluations missing")
    if text_ev is None:
        return vision_ev  # type: ignore[return-value]
    if vision_ev is None:
        return text_ev

    # Agreement — no LLM call needed
    if text_ev.status == vision_ev.status:
        return RuleEvaluation(
            rule_id=rule.id,
            status=text_ev.status,
            severity=text_ev.severity or vision_ev.severity,
            confidence=min(text_ev.confidence, vision_ev.confidence),
            reasoning=f"[Agreed] {text_ev.reasoning}",
            evidence=text_ev.evidence or vision_ev.evidence,
            description=text_ev.description or vision_ev.description,
            recommendation=text_ev.recommendation or vision_ev.recommendation,
            applicability_trace=list(text_ev.applicability_trace),
        )

    # Conflict — attempt LLM arbitration
    if llm is not None:
        try:
            arbitrated = await _call_arbitrator(rule, text_ev, vision_ev, llm, ocr_text)
            return RuleEvaluation(
                rule_id=rule.id,
                status=arbitrated.status,
                severity=arbitrated.severity or text_ev.severity or vision_ev.severity,
                confidence=arbitrated.confidence,
                reasoning=(
                    f"[Arbitrated] {arbitrated.reasoning} "
                    f"| Text: {text_ev.reasoning} "
                    f"| Vision: {vision_ev.reasoning}"
                ),
                evidence=arbitrated.evidence or text_ev.evidence or vision_ev.evidence,
                description=arbitrated.description or text_ev.description or vision_ev.description,
                recommendation=arbitrated.recommendation or text_ev.recommendation or vision_ev.recommendation,
                applicability_trace=list(text_ev.applicability_trace),
            )
        except Exception as exc:
            logger.warning("LLM arbitration failed for rule %s: %s — falling back to higher severity", rule.id, exc)
            try:
                from app.observability.run_telemetry import record_event  # noqa: PLC0415
                record_event(
                    "compliance.arbitration_fallback_used",
                    level="warning",
                    rule_id=rule.id,
                    reason=f"arbitration_raised: {type(exc).__name__}: {exc}"[:200],
                    text_status=text_ev.status,
                    vision_status=vision_ev.status,
                )
            except Exception:  # pragma: no cover — never break eval
                pass

    # Fallback — higher severity wins. Fires either when the
    # arbitration LLM raised (already recorded above) OR when no
    # LLM was provided — record the latter case so post-run
    # analysis can distinguish "arbitrator broken" from "arbitrator
    # not wired" in deployments where llm_arbitrated rules run
    # without an LLM dependency. The first branch's exception path
    # has already emitted a more specific event.
    if llm is None:
        try:
            from app.observability.run_telemetry import record_event  # noqa: PLC0415
            record_event(
                "compliance.arbitration_fallback_used",
                level="warning",
                rule_id=rule.id,
                reason="no_llm_provided",
                text_status=text_ev.status,
                vision_status=vision_ev.status,
            )
        except Exception:  # pragma: no cover — never break eval
            pass

    text_sev = _STATUS_SEVERITY.get(text_ev.status, 0)
    vision_sev = _STATUS_SEVERITY.get(vision_ev.status, 0)
    winner = text_ev if text_sev >= vision_sev else vision_ev
    return RuleEvaluation(
        rule_id=rule.id,
        status=winner.status,
        severity=winner.severity or (vision_ev if winner is text_ev else text_ev).severity,
        confidence=min(text_ev.confidence, vision_ev.confidence),
        reasoning=f"[Fallback-higher-sev] {winner.reasoning}",
        evidence=winner.evidence,
        description=winner.description,
        recommendation=winner.recommendation,
        applicability_trace=list(text_ev.applicability_trace),
    )


def _build_document_scope_prompt(
    rules: list[AuditRule],
    summary_content: str,
) -> str:
    """Prompt for document-level rules (not per-page)."""
    rules_section = "\n".join(_format_rule_for_prompt(r) for r in rules)
    return (
        f"Evaluate ONLY the following {len(rules)} rules against this DOCUMENT-LEVEL summary.\n\n"
        f"These rules are about document-wide properties (archival, retention, "
        f"availability, backup) — NOT about individual page content.\n"
        f"Assess whether the document as a whole satisfies each rule based on the "
        f"summary information provided.\n\n"
        f"IMPORTANT RULE GUIDANCE:\n"
        f"- Each rule below may include PASS CRITERIA and SKIP IF conditions.\n"
        f"- PASS CRITERIA tells you exactly what constitutes compliance — follow it strictly.\n"
        f"- SKIP IF conditions tell you when to mark the rule not_applicable.\n\n"
        f"For each rule, return a JSON object with:\n"
        f'  rule_id, status ("compliant"|"non_compliant"|"not_applicable"|"uncertain"),\n'
        f"  confidence (float 0.0-1.0),\n"
        f"  severity (only if non_compliant),\n"
        f"  reasoning (REQUIRED: 1-3 sentence explanation),\n"
        f"  evidence (REQUIRED: reference specific content from the summary),\n"
        f"  description (what the issue is — empty only if compliant),\n"
        f"  recommendation (empty only if compliant).\n\n"
        f"RULES TO EVALUATE:\n{rules_section}\n\n"
        f"{summary_content}"
    )


async def run_document_scope_evaluation(
    agent: str,
    batches: list[RuleBatch],
    extractions: list[dict],
    llm: LLMProvider,
) -> list[tuple[str, int | None, RuleBatchResult]]:
    """Evaluate document-scope rules once against a document summary.

    Rules with evaluation_mode="cannot_evaluate" are resolved without LLM calls.
    """
    if not batches or not extractions:
        return []

    first = extractions[0]
    last = extractions[-1] if len(extractions) > 1 else first
    total_pages = len(extractions)

    summary_parts = [
        f"DOCUMENT SUMMARY ({total_pages} pages total):\n",
        f"--- First page (page {first.get('page_num', 1)}) ---\n{first.get('markdown', '')[:2000]}\n",
    ]
    if last is not first:
        summary_parts.append(
            f"--- Last page (page {last.get('page_num', total_pages)}) ---\n{last.get('markdown', '')[:2000]}\n"
        )

    hw_total = sum(e.get("handwritten_count", 0) for e in extractions)
    sig_total = sum(len(e.get("signatures", [])) for e in extractions)
    summary_parts.append(
        f"--- Document-level stats ---\n"
        f"Total pages: {total_pages}\n"
        f"Total handwritten words across document: {hw_total}\n"
        f"Total signature fields across document: {sig_total}\n"
    )

    summary_content = "\n".join(summary_parts)
    system = _SYSTEM_PROMPTS.get(agent, _SYSTEM_PROMPTS["alcoa"])

    results: list[tuple[str, int | None, RuleBatchResult]] = []
    for batch in batches:
        # Separate cannot-evaluate rules from LLM-evaluable rules
        llm_rules: list[AuditRule] = []
        gate_evals: list[RuleEvaluation] = []
        for rule in batch.rules:
            if rule.evaluation_mode == "cannot_evaluate":
                gate_evals.append(RuleEvaluation(
                    rule_id=rule.id,
                    status="not_applicable",
                    confidence=1.0,
                    reasoning=rule.cannot_evaluate_reason or "Rule requires external data not available",
                ))
            else:
                llm_rules.append(rule)

        if not llm_rules:
            results.append((batch.batch_id, None, RuleBatchResult(evaluations=gate_evals)))
            continue

        prompt = _build_document_scope_prompt(llm_rules, summary_content)
        try:
            result = await llm.generate_structured(prompt, RuleBatchResult, system=system)
            if not isinstance(result, RuleBatchResult):
                result = RuleBatchResult.model_validate(result)
            result.evaluations.extend(gate_evals)
        except Exception:
            logger.exception("Document-scope batch %s failed", batch.batch_id)
            result = RuleBatchResult(evaluations=[
                RuleEvaluation(rule_id=r.id, status="error", description="Evaluation failed")
                for r in llm_rules
            ] + gate_evals)
        results.append((batch.batch_id, None, result))

    return results


def _compute_hitl_status(confidence: float) -> str:
    """Determine HITL status from confidence score."""
    settings = get_settings()
    threshold = settings.hitl.auto_approve_threshold
    if confidence >= threshold:
        return "auto_approved"
    return "needs_review"


def assemble_agent_report(
    agent: str,
    all_rules: list[AuditRule],
    batch_results: list[tuple[str, int | None, RuleBatchResult]],
    pages_reviewed: list[int],
) -> AgentReport:
    """Assemble an AgentReport from batch evaluation results."""
    rule_map = {r.id: r for r in all_rules}

    finding_counter = 0
    all_findings: list[ComplianceFinding] = []
    raw_eval_map: dict[str, RuleResult] = {}
    per_rule_worst: dict[str, str] = {}

    for _batch_id, page_num, result in batch_results:
        for ev in result.evaluations:
            rule = rule_map.get(ev.rule_id)
            if not rule:
                continue

            status = ev.status if ev.status in _VALID_STATUSES else "uncertain"
            confidence = max(0.0, min(1.0, ev.confidence))
            pn_list: list[int] = [page_num] if page_num is not None else []

            # Track worst status per rule for scoring (M1)
            prev = per_rule_worst.get(ev.rule_id, "not_applicable")
            if _STATUS_SEVERITY.get(status, 0) > _STATUS_SEVERITY.get(prev, 0):
                per_rule_worst[ev.rule_id] = status

            # Merge into audit trail (H2: keep worst status)
            if ev.rule_id in raw_eval_map:
                existing_re = raw_eval_map[ev.rule_id]
                for pn in pn_list:
                    if pn not in existing_re.page_numbers:
                        existing_re.page_numbers.append(pn)
                if _STATUS_SEVERITY.get(status, 0) > _STATUS_SEVERITY.get(existing_re.status, 0):
                    existing_re.status = status
                existing_re.confidence = min(existing_re.confidence, confidence)
                if ev.reasoning and not existing_re.reasoning:
                    existing_re.reasoning = ev.reasoning
                if ev.evidence and not existing_re.evidence:
                    existing_re.evidence = ev.evidence
                for step in ev.applicability_trace:
                    if step not in existing_re.applicability_trace:
                        existing_re.applicability_trace.append(step)
            else:
                raw_eval_map[ev.rule_id] = RuleResult(
                    rule_id=ev.rule_id,
                    rule_text=rule.text,
                    rule_category=rule.category,
                    agent=agent,
                    status=status,
                    confidence=confidence,
                    reasoning=ev.reasoning,
                    evidence=ev.evidence,
                    applicability_trace=list(ev.applicability_trace),
                    page_numbers=pn_list,
                )

            # H1: only create findings for real compliance issues, not errors
            if status in ("non_compliant", "uncertain"):
                finding_counter += 1
                hitl_status = _compute_hitl_status(confidence)

                # Determine evaluation channels and visual evidence from reasoning tags
                eval_channels: list[str] = []
                visual_evidence_text = ""
                strategy = rule.evaluation_strategy if rule else "text"
                if strategy == "vision":
                    eval_channels = ["vision"]
                elif strategy == "text_and_vision":
                    eval_channels = ["text", "vision"]
                else:
                    eval_channels = ["text"]

                reasoning = ev.reasoning or ""
                if "[Vision]" in reasoning:
                    parts = reasoning.split("[Vision]")
                    if len(parts) > 1:
                        visual_evidence_text = parts[-1].strip().rstrip("]").strip()

                finding = ComplianceFinding(
                    finding_id=f"{agent}-{finding_counter}",
                    rule_id=ev.rule_id,
                    rule_text=rule.text,
                    rule_category=rule.category,
                    rule_category_display=rule.category_display,
                    agent=agent,
                    severity=ev.severity or rule.severity_hint,
                    status=status,
                    confidence=confidence,
                    page_numbers=pn_list,
                    reasoning=ev.reasoning,
                    evidence=ev.evidence,
                    description=ev.description,
                    recommendation=ev.recommendation,
                    applicability_trace=list(ev.applicability_trace),
                    hitl_status=hitl_status,
                    evaluation_channels=eval_channels,
                    visual_evidence=visual_evidence_text,
                )
                all_findings.append(finding)

    deduped = _deduplicate_findings(all_findings)

    severity_counts: dict[str, int] = defaultdict(int)
    for f in deduped:
        severity_counts[f.severity] += 1

    # M1: Per-rule scoring — group by category, use worst-status per rule
    cat_rule_statuses: dict[str, dict[str, str]] = defaultdict(dict)
    for rule_id, worst in per_rule_worst.items():
        rule = rule_map.get(rule_id)
        if rule:
            cat_rule_statuses[rule.category][rule_id] = worst

    cat_displays = {r.category: r.category_display for r in all_rules}
    cat_scores: list[CategoryScore] = []
    for cat_id, rule_statuses in cat_rule_statuses.items():
        compliant = sum(1 for s in rule_statuses.values() if s == "compliant")
        non_compliant = sum(1 for s in rule_statuses.values() if s == "non_compliant")
        not_applicable = sum(1 for s in rule_statuses.values() if s == "not_applicable")
        uncertain = sum(1 for s in rule_statuses.values() if s == "uncertain")
        error_count = sum(1 for s in rule_statuses.values() if s == "error")
        applicable = len(rule_statuses) - not_applicable - error_count
        score = (compliant / max(applicable, 1)) * 100.0 if applicable > 0 else 100.0

        cat_finding_ids = [f.finding_id for f in deduped if f.rule_category == cat_id]

        cat_scores.append(CategoryScore(
            category_id=cat_id,
            category_display=cat_displays.get(cat_id, cat_id),
            agent=agent,
            score=round(score, 1),
            total_rules=len(rule_statuses),
            compliant=compliant,
            non_compliant=non_compliant,
            not_applicable=not_applicable,
            uncertain=uncertain,
            finding_ids=cat_finding_ids,
        ))

    total_rules_scored = len(per_rule_worst)
    total_compliant = sum(1 for s in per_rule_worst.values() if s == "compliant")
    total_na = sum(1 for s in per_rule_worst.values() if s == "not_applicable")
    total_error = sum(1 for s in per_rule_worst.values() if s == "error")
    applicable = total_rules_scored - total_na - total_error
    agent_score = (total_compliant / max(applicable, 1)) * 100.0 if applicable > 0 else 100.0

    return AgentReport(
        agent=agent,
        agent_display=AGENT_DISPLAY_NAMES.get(agent, agent),
        score=round(agent_score, 1),
        model_score=round(agent_score, 1),
        total_rules=len(all_rules),
        total_findings=len(deduped),
        severity_counts=dict(severity_counts),
        category_scores=cat_scores,
        findings=deduped,
        all_evaluations=sorted(raw_eval_map.values(), key=lambda r: r.rule_id),
        pages_reviewed=sorted(set(pages_reviewed)),
    )


def _deduplicate_findings(
    findings: list[ComplianceFinding],
    *,
    mode: str = "per_agent",
) -> list[ComplianceFinding]:
    """Merge findings for the same rule.

    ``mode`` (see research §R7):

    * ``"per_agent"`` — **default** — dedup by ``rule_id`` alone. Correct for
      the inside-one-agent call (same rule fires on multiple pages).
    * ``"cross_agent_preserve"`` — dedup by ``(agent, rule_id)`` so two
      agents that both match the same rule each keep their finding.
      Prevents the silent attribution loss that made ``AgentReport.
      total_findings`` drift from the global filtered count.
    * ``"cross_agent_collapse"`` — legacy behaviour: collapse across
      agents, first-seen wins. Callers opting in must resync
      ``AgentReport.total_findings`` themselves via
      :func:`resync_agent_totals` below.

    Every collapse in modes other than ``"per_agent"`` emits a
    ``compliance.finding.deduped`` log + increments
    ``compliance_dedup_merges_total{mode}`` (FR-015).
    """

    if mode not in ("per_agent", "cross_agent_preserve", "cross_agent_collapse"):
        raise ValueError(f"unknown dedup mode: {mode!r}")

    def _key(f: ComplianceFinding) -> tuple[str, ...]:
        if mode == "cross_agent_preserve":
            return (f.agent, f.rule_id)
        return (f.rule_id,)

    # Defer observability imports — these paths are used from tests with a
    # fresh metric registry and we don't want import-time coupling.
    try:
        from app.observability import get_logger
        from app.observability.metrics import COMPLIANCE_DEDUP_MERGES

        _slog = get_logger(__name__)
    except Exception:  # pragma: no cover — fail-open
        _slog = None
        COMPLIANCE_DEDUP_MERGES = None  # type: ignore[assignment]

    merge_tracker: dict[tuple[str, ...], list[str]] = {}
    by_key: dict[tuple[str, ...], ComplianceFinding] = {}
    for f in findings:
        k = _key(f)
        if k in by_key:
            existing = by_key[k]
            for pn in f.page_numbers:
                if pn not in existing.page_numbers:
                    existing.page_numbers.append(pn)
            if f.severity in SEVERITY_WEIGHTS and existing.severity in SEVERITY_WEIGHTS:
                if SEVERITY_WEIGHTS[f.severity] > SEVERITY_WEIGHTS[existing.severity]:
                    existing.severity = f.severity
            if f.evidence and not existing.evidence:
                existing.evidence = f.evidence
            if f.reasoning and not existing.reasoning:
                existing.reasoning = f.reasoning
            for step in f.applicability_trace:
                if step not in existing.applicability_trace:
                    existing.applicability_trace.append(step)
            existing.confidence = min(existing.confidence, f.confidence)
            existing.hitl_status = _compute_hitl_status(existing.confidence)
            if mode != "per_agent":
                dropped = merge_tracker.setdefault(k, [existing.agent])
                if f.agent not in dropped:
                    dropped.append(f.agent)
        else:
            by_key[k] = f.model_copy()
            if mode != "per_agent":
                merge_tracker[k] = [f.agent]

    # Emit observability signals for cross-agent collapses only.
    if mode == "cross_agent_collapse":
        for key, agents in merge_tracker.items():
            if len(agents) > 1:
                winner = by_key[key].agent
                dropped = [a for a in agents if a != winner]
                if _slog is not None:
                    _slog.info(
                        "compliance.finding.deduped",
                        rule_id=key[-1],
                        winner_agent=winner,
                        dropped_agents=dropped,
                        mode=mode,
                    )
                if COMPLIANCE_DEDUP_MERGES is not None:
                    try:
                        COMPLIANCE_DEDUP_MERGES.labels(mode=mode).inc()
                    except Exception:  # pragma: no cover
                        pass

    return sorted(
        by_key.values(),
        key=lambda f: SEVERITY_WEIGHTS.get(f.severity, 0),
        reverse=True,
    )


def resync_agent_totals(
    agent_reports: list[AgentReport],
    global_findings: list[ComplianceFinding],
) -> None:
    """After a cross-agent collapse, rebalance ``AgentReport.total_findings``.

    When the global dedup drops findings in favour of another agent's
    copy, the losing agent's ``total_findings`` would still reflect its
    pre-global count. This function recounts per-agent occurrences in
    the globally-surviving list so tab badges match filtered rows.
    """

    from collections import Counter

    by_agent = Counter(f.agent for f in global_findings)
    for ar in agent_reports:
        resynced = by_agent.get(ar.agent, 0)
        if resynced != ar.total_findings:
            try:
                from app.observability import get_logger

                get_logger(__name__).info(
                    "compliance.agent.total_resynced",
                    agent=ar.agent,
                    previous=ar.total_findings,
                    resynced=resynced,
                )
            except Exception:  # pragma: no cover
                pass
            ar.total_findings = resynced
