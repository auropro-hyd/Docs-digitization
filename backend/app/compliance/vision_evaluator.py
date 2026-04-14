"""VisionBatchEvaluator: evaluates vision-tagged rules against page images.

Parallels ``RuleBatchEvaluator`` but sends page images to a VLM provider
instead of OCR text to an LLM.  Each visual check (``VC-*``) has a
domain-specific prompt template optimised for pharmaceutical document
inspection.
"""

from __future__ import annotations

import logging
from typing import Sequence

from app.compliance.models import (
    RuleBatchResult,
    RuleEvaluation,
    VisionBatchResult,
    VisualCheckResult,
)
from app.compliance.rules.registry import AuditRule, RuleBatch
from app.core.ports.vlm import VLMProvider

logger = logging.getLogger(__name__)

# ── Per-check prompt templates ────────────────────────────────────────

_VC_PROMPTS: dict[str, str] = {
    "VC-STRIKE": (
        "Analyze this page image for correction methodology compliance.\n\n"
        "For each correction visible on the page:\n"
        "1. Is it a SINGLE-LINE strikethrough (GMP-compliant)?\n"
        "2. Or is it a scribble, multiple lines, or heavy crossing-out (non-compliant)?\n"
        "3. Is the original text still READABLE beneath the correction?\n"
        "4. Are there INITIALS and a DATE adjacent to the correction?\n\n"
        "Report each correction found with its location (top/middle/bottom of page), "
        "type (single-line/double-line/scribble/other), original text readability "
        "(readable/partially-readable/illegible), and whether initials+date are present."
    ),
    "VC-SIGNATURE": (
        "Analyze this page image for signature field compliance.\n\n"
        "For each area that appears to be a signature or identity field:\n"
        "1. Does it contain a HANDWRITTEN signature (wet ink mark)?\n"
        "2. Or is it TYPED text (e.g., a printed name)?\n"
        "3. Or is it a RUBBER STAMP impression?\n"
        "4. Or is it EMPTY/BLANK?\n"
        "5. Is there an accompanying DATE?\n\n"
        "A wet handwritten signature or handwritten initials = compliant.\n"
        "Typed text alone in a signature field = observation (may need policy review).\n"
        "Empty signature field where one is required = non-compliant."
    ),
    "VC-INK-COLOR": (
        "Analyze this page image for ink color compliance.\n\n"
        "For handwritten entries visible on this page:\n"
        "1. What COLOR ink was used? (blue/black/red/green/pencil/other)\n"
        "2. Are there any entries made in PENCIL (graphite)?\n"
        "3. Are there entries in non-standard colors for non-annotation purposes?\n\n"
        "Per GMP requirements:\n"
        "- Blue or black ink = compliant\n"
        "- Pencil (graphite) = non-compliant (not permanent)\n"
        "- Red ink for annotations/corrections only = acceptable\n"
        "- Red ink for primary entries = observation"
    ),
    "VC-CORRECTION": (
        "Analyze this page image for prohibited correction methods.\n\n"
        "Look for evidence of:\n"
        "1. WHITE-OUT / CORRECTION FLUID (opaque white patches covering text)\n"
        "2. ERASURE marks (rubbed/smudged areas, especially on handwritten text)\n"
        "3. OVERWRITING (new text written directly over old text without strikethrough)\n"
        "4. TAPE corrections (transparent or opaque tape covering original entries)\n\n"
        "Any of these correction methods is a CRITICAL non-compliance finding "
        "in GMP-regulated documents."
    ),
    "VC-STAMP-SEAL": (
        "Analyze this page image for official stamps, seals, and watermarks.\n\n"
        "Look for:\n"
        "1. 'ORIGINAL' stamp or watermark\n"
        "2. 'CONTROLLED COPY' markings\n"
        "3. 'COPY' watermarks indicating this is NOT the original\n"
        "4. QA/QC approval stamps\n"
        "5. Document control number stamps\n\n"
        "Report each stamp/seal/watermark found with its text content and location."
    ),
    "VC-ATTACHMENT": (
        "Analyze this page image for physical attachments and their integrity.\n\n"
        "Look for:\n"
        "1. Affixed labels or stickers (chromatogram printouts, balance tickets)\n"
        "2. Pasted documents or printouts\n"
        "3. Evidence of loose or detached attachments\n"
        "4. Missing attachments (empty spaces where items were previously affixed)\n"
        "5. Attachment reference labels and numbering\n\n"
        "Report attachment condition: intact/partially-detached/missing."
    ),
    "VC-BARCODE": (
        "Analyze this page image for barcode and label quality.\n\n"
        "For each barcode or label visible:\n"
        "1. Is the barcode clear and likely scannable?\n"
        "2. Or is it smudged, damaged, or partially obscured?\n"
        "3. Is the label text legible?\n\n"
        "Report barcode condition: clear/smudged/damaged/partially-obscured."
    ),
    "VC-BLANK-FIELD": (
        "Analyze this page image for blank form fields.\n\n"
        "For each form field or table cell visible:\n"
        "1. Is it filled with text, a value, a dash, or 'N/A'?\n"
        "2. Or is it completely BLANK (empty white space)?\n"
        "3. Note: a dash (-) or 'N/A' is a valid entry, NOT blank.\n\n"
        "Only flag fields that are truly empty with no mark whatsoever. "
        "Report the location and expected content type of each blank field."
    ),
    "VC-DOC-QUALITY": (
        "Analyze this page image for physical document quality.\n\n"
        "Look for:\n"
        "1. SMUDGES or stains that impair readability\n"
        "2. FADING — text or ink that has faded over time\n"
        "3. WATER DAMAGE — warping, discoloration, ink bleeding\n"
        "4. TEARS or physical damage to the page\n"
        "5. PENCIL marks (non-permanent annotations)\n\n"
        "Minor cosmetic marks that do not affect GMP-critical data are acceptable."
    ),
    "VC-CHART": (
        "Analyze this page image for chart/graph quality and labeling.\n\n"
        "For each chart, graph, or figure:\n"
        "1. Are axis LABELS present and legible?\n"
        "2. Are SCALE markings visible and accurate?\n"
        "3. Are UNITS displayed on each axis?\n"
        "4. Is the data plot clear and readable?\n"
        "5. Is there a title or legend?\n\n"
        "Report the quality of each chart/graph found."
    ),
    "VC-CHROMATOGRAM": (
        "Analyze this page image for chromatogram/spectra integrity.\n\n"
        "Look for:\n"
        "1. Is the chromatogram plot clear and unobstructed?\n"
        "2. Are peak labels visible?\n"
        "3. Is baseline clearly defined?\n"
        "4. Are integration marks present?\n"
        "5. Is the printout metadata visible (run date, method, operator)?\n\n"
        "Report chromatogram condition: intact/partial/damaged."
    ),
    "VC-CHECKBOX": (
        "Analyze this page image for checkbox/tickmark status.\n\n"
        "For each checkbox or checklist item visible:\n"
        "1. Is it CHECKED (has a tick, check mark, X, or filled)?\n"
        "2. Or is it UNCHECKED (empty box)?\n"
        "3. Or is it marked 'N/A'?\n\n"
        "Report the total number of checked, unchecked, and N/A items found."
    ),
    "VC-PAGINATION": (
        "Analyze this page image for page numbering.\n\n"
        "Look in headers and footers for:\n"
        "1. 'Page X of Y' format\n"
        "2. Any page number reference\n"
        "3. Is the page number legible?\n"
        "4. Is there evidence of binding order?\n\n"
        "Report whether page numbering is visible and legible."
    ),
    "VC-STICKY-NOTE": (
        "Analyze this page image for temporary annotations.\n\n"
        "Look for:\n"
        "1. STICKY NOTES (Post-it notes) on the page\n"
        "2. PENCIL annotations or markings\n"
        "3. DRAFT watermarks or stamps\n"
        "4. Temporary labels or tags\n"
        "5. Any non-permanent markings\n\n"
        "Any temporary annotation on a GMP document is non-compliant."
    ),
}

_VISION_SYSTEM_PROMPT = (
    "You are a pharmaceutical compliance visual inspector specializing in "
    "GMP (Good Manufacturing Practice) document review. You analyze page "
    "images from pharmaceutical batch production records, logbooks, and "
    "controlled documents.\n\n"
    "CRITICAL GUIDELINES:\n"
    "1. Base your assessment ONLY on what is visually present in the image.\n"
    "2. Be precise about locations — describe as top/middle/bottom, left/center/right.\n"
    "3. Distinguish between genuine compliance issues and normal document features.\n"
    "4. For each rule, provide a clear status: compliant, non_compliant, "
    "not_applicable, or uncertain.\n"
    "5. Confidence should reflect your certainty: 0.9+ for clear visual evidence, "
    "0.6-0.8 for moderate clarity, <0.6 for ambiguous.\n"
    "6. When multiple visual checks are requested, evaluate each independently."
)


def _collect_visual_checks(rules: Sequence[AuditRule]) -> set[str]:
    """Collect unique VC-* check IDs needed across a set of rules."""
    checks: set[str] = set()
    for rule in rules:
        for vc in rule.visual_checks:
            checks.add(vc)
    return checks


def _build_vision_prompt(rules: Sequence[AuditRule], page_num: int) -> str:
    """Compose a VLM prompt from the union of visual checks needed."""
    checks = _collect_visual_checks(rules)

    prompt_parts: list[str] = [
        f"Evaluate the following {len(rules)} compliance rules against this "
        f"page image (page {page_num}).\n",
    ]

    prompt_parts.append("\n--- VISUAL CHECKS REQUIRED ---\n")
    for vc_id in sorted(checks):
        template = _VC_PROMPTS.get(vc_id)
        if template:
            prompt_parts.append(f"\n[{vc_id}]\n{template}\n")

    prompt_parts.append("\n--- RULES TO EVALUATE ---\n")
    for rule in rules:
        vc_tags = ", ".join(rule.visual_checks) if rule.visual_checks else "general"
        lines = [f"- [{rule.id}] (checks: {vc_tags}) {rule.text}"]
        if rule.pass_criteria:
            lines.append(f"  PASS CRITERIA: {rule.pass_criteria}")
        if rule.skip_conditions:
            for cond in rule.skip_conditions:
                lines.append(f"  SKIP IF: {cond}")
        prompt_parts.append("\n".join(lines))

    prompt_parts.append(
        "\n\n--- OUTPUT FORMAT ---\n"
        "For each rule return a JSON object with:\n"
        '  rule_id, status ("compliant"|"non_compliant"|"not_applicable"|"uncertain"),\n'
        "  confidence (float 0.0-1.0),\n"
        '  severity (only if non_compliant: "critical"|"major"|"minor"|"observation"),\n'
        "  reasoning (1-3 sentences referencing visual evidence),\n"
        "  evidence (describe what you see in the image),\n"
        "  description (what the issue is — empty if compliant),\n"
        "  recommendation (remediation — empty if compliant).\n\n"
        "Also return visual_checks results with check_id, detected (bool), "
        "classification, confidence, and description for each VC-* check performed."
    )

    return "\n".join(prompt_parts)


class VisionBatchEvaluator:
    """Evaluates vision-tagged rules against page images via VLM."""

    _MAX_RETRIES = 2

    async def evaluate_batch(
        self,
        batch: RuleBatch,
        page_image: bytes,
        page_num: int,
        vlm: VLMProvider,
    ) -> tuple[str, int, RuleBatchResult]:
        """Evaluate vision rules in *batch* against *page_image*.

        Returns ``(batch_id, page_num, result)`` matching the text evaluator
        signature for seamless merging.
        """
        if not page_image:
            evals = [
                RuleEvaluation(
                    rule_id=r.id,
                    status="not_applicable",
                    reasoning="No page image available for visual inspection",
                )
                for r in batch.rules
            ]
            return batch.batch_id, page_num, RuleBatchResult(evaluations=evals)

        prompt = _build_vision_prompt(batch.rules, page_num)
        last_exc: Exception | None = None

        for attempt in range(1 + self._MAX_RETRIES):
            try:
                if vlm.supports_structured_output():
                    result = await vlm.analyze_image_structured(
                        page_image,
                        prompt,
                        VisionBatchResult,
                        system=_VISION_SYSTEM_PROMPT,
                    )
                    if not isinstance(result, VisionBatchResult):
                        result = VisionBatchResult.model_validate(result)
                else:
                    raw = await vlm.analyze_image(
                        page_image, prompt, system=_VISION_SYSTEM_PROMPT,
                    )
                    import json
                    parsed = json.loads(raw)
                    result = VisionBatchResult.model_validate(parsed)

                return self._to_batch_result(batch, page_num, result)

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Vision batch %s page %d attempt %d failed: %s",
                    batch.batch_id, page_num, attempt + 1, exc,
                )

        logger.error(
            "Vision batch %s page %d exhausted retries: %s",
            batch.batch_id, page_num, last_exc,
        )
        evals = [
            RuleEvaluation(
                rule_id=r.id,
                status="error",
                description=f"Vision evaluation failed: {last_exc}",
            )
            for r in batch.rules
        ]
        return batch.batch_id, page_num, RuleBatchResult(evaluations=evals)

    def _to_batch_result(
        self,
        batch: RuleBatch,
        page_num: int,
        vision_result: VisionBatchResult,
    ) -> tuple[str, int, RuleBatchResult]:
        """Convert VLM output to the standard ``RuleBatchResult``."""
        check_map: dict[str, VisualCheckResult] = {
            c.check_id: c for c in vision_result.checks
        }

        evaluations: list[RuleEvaluation] = []
        evaluated_ids: set[str] = set()

        for ev in vision_result.rule_evaluations:
            evaluated_ids.add(ev.rule_id)
            evaluations.append(ev)

        for rule in batch.rules:
            if rule.id in evaluated_ids:
                continue
            status, reasoning = self._infer_from_checks(rule, check_map)
            evaluations.append(RuleEvaluation(
                rule_id=rule.id,
                status=status,
                confidence=0.7,
                reasoning=reasoning,
                evidence="Inferred from visual check results",
            ))

        return batch.batch_id, page_num, RuleBatchResult(evaluations=evaluations)

    def _infer_from_checks(
        self,
        rule: AuditRule,
        check_map: dict[str, VisualCheckResult],
    ) -> tuple[str, str]:
        """Infer rule status from visual check results when VLM didn't
        return a direct rule evaluation."""
        if not rule.visual_checks:
            return "not_applicable", "No visual checks configured for this rule"

        relevant = [check_map[vc] for vc in rule.visual_checks if vc in check_map]
        if not relevant:
            return "uncertain", "Visual checks were requested but no results returned"

        for check in relevant:
            if check.check_id in ("VC-CORRECTION", "VC-STICKY-NOTE"):
                if check.detected:
                    return "non_compliant", check.description or f"{check.check_id} detected"
            elif check.check_id == "VC-INK-COLOR":
                if check.detected and "pencil" in check.classification.lower():
                    return "non_compliant", "Pencil entries detected"
            elif check.check_id == "VC-BLANK-FIELD":
                if check.detected:
                    return "non_compliant", check.description or "Blank fields detected"

        return "compliant", "Visual inspection passed"
