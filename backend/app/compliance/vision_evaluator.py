"""VisionBatchEvaluator: evaluates vision-tagged rules against page images.

Parallels ``RuleBatchEvaluator`` but sends page images to a VLM provider
instead of OCR text to an LLM.  Each visual check (``VC-*``) has a
domain-specific prompt template optimised for pharmaceutical document
inspection.

Prompt-design discipline (PR #26):

The ``VC-INK-COLOR`` false positives Akhilesh hit on 2026-04-30 were
the visible symptom of a pattern present across most of the original
VC-* prompts:

  1. **No absence escape.** "For each X visible, …" or "Look for evidence
     of Y" primes the VLM to find X / Y. When nothing is present, the
     VLM tends to invent something rather than emit a "nothing found"
     reply. Each prompt now explicitly tells the VLM what to do when
     the target is absent (= ``compliant``, not ``non_compliant`` or
     made-up findings).

  2. **High-stakes amplifiers.** Phrases like "any of these is a
     CRITICAL non-compliance finding" turn a low-confidence VLM read
     into a ``critical`` finding downstream. The amplifier stays for
     genuinely-detected issues but doesn't apply to ambiguous reads —
     the system prompt now requires the model to use ``uncertain``
     for sub-0.6 confidence rather than returning ``non_compliant``.

  3. **Counterfactual asks.** ``VC-ATTACHMENT`` asked the VLM to
     detect "missing attachments (empty spaces where items were
     previously affixed)" — a thing only inferrable from context the
     VLM doesn't have. We narrow that to evidence-based detachment
     (visible adhesive residue, torn corners, attachment-reference
     text pointing at empty space).

  4. **Counting tasks.** ``VC-CHECKBOX`` asked for total counts of
     checked / unchecked items. VLMs are notoriously bad at counting;
     we move to coarse buckets ("most/mixed/few") which the VLM can
     judge reliably.

The ``llm_arbitrated`` evaluation strategy on Akhilesh's 008/009
branches reconciles OCR-vs-vision conflicts; the cleaner the VLM
output, the less work that arbitrator does.
"""

from __future__ import annotations

import logging
from typing import Sequence

from app.compliance.image_color_check import image_has_meaningful_colour
from app.compliance.models import (
    RuleBatchResult,
    RuleEvaluation,
    VisionBatchResult,
    VisualCheckResult,
)
from app.compliance.rules.registry import AuditRule, RuleBatch
from app.core.ports.vlm import VLMProvider

logger = logging.getLogger(__name__)

# Visual checks that depend on chromatic information. When the page
# image is a B&W scan, asking the VLM to evaluate these is at best
# wasted spend and at worst a false-positive generator (the VLM
# guesses a colour from stroke contrast). The pre-flight guard
# short-circuits these to ``not_applicable`` before the VLM call.
_COLOUR_DEPENDENT_CHECKS: frozenset[str] = frozenset({
    "VC-INK-COLOR",
})

# ── Per-check prompt templates ────────────────────────────────────────

_VC_PROMPTS: dict[str, str] = {
    "VC-STRIKE": (
        "Analyze this page image for correction methodology compliance.\n\n"
        "**ABSENCE FIRST.** Most BPCR pages carry NO corrections. If you "
        "do not see any deliberate strikethrough or crossing-out marks "
        "on this page, the correct answer is ``compliant`` with the "
        "evidence \"no corrections present on this page.\" The following "
        "are NOT corrections and must not be reported as such: form "
        "lines, table borders, signature underlines, dashes ('-') used "
        "as N/A markers, decorative rules, page-number separators.\n\n"
        "If genuine corrections ARE visible, for each one:\n"
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
        "**CRITICAL FIRST STEP — GRAYSCALE GUARD:**\n"
        "Before answering anything else, decide: does this image actually "
        "carry colour information, or is it a black-and-white / grayscale "
        "scan? A B&W scan has zero hue saturation — every mark on it is "
        "some shade of gray. If you cannot see clearly saturated chromatic "
        "ink (a visibly red, green, or blue pen stroke whose hue is "
        "obvious, not just a darker mark), the correct answer is "
        "GRAYSCALE — emit ``not_applicable`` with reasoning "
        "\"page is a grayscale scan; ink colour cannot be determined from "
        "this rendering\". DO NOT guess a colour from contrast or stroke "
        "density alone — a heavy black-ink mark looks dark, not red.\n\n"
        "Only proceed past this guard when you can clearly identify ink "
        "hue. For each handwritten entry visible:\n"
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
        "**ABSENCE FIRST.** Prohibited corrections are RARE in real "
        "BPCRs. The default answer for almost every page is "
        "``compliant`` with evidence \"no prohibited correction "
        "methods visible.\" Be especially careful that the following "
        "are NOT prohibited corrections: blank cells with white "
        "background, light printer toner, naturally lighter form "
        "fields, paper texture, JPEG compression artifacts, or "
        "underlines/borders. White-out and erasure detection from a "
        "scanned page is technically hard — only flag a correction "
        "when you can see UNAMBIGUOUS evidence (a thick opaque "
        "white patch with visible boundary; a clear smudge specific "
        "to a handwritten entry; tape with reflective edges).\n\n"
        "When prohibited corrections ARE visible, classify them:\n"
        "1. WHITE-OUT / CORRECTION FLUID (opaque white patches covering text)\n"
        "2. ERASURE marks (rubbed/smudged areas, especially on handwritten text)\n"
        "3. OVERWRITING (new text written directly over old text without strikethrough)\n"
        "4. TAPE corrections (transparent or opaque tape covering original entries)\n\n"
        "If your confidence in the detection is below 0.7, return "
        "``uncertain`` rather than ``non_compliant`` — the downstream "
        "arbitrator will reconcile with OCR signal. A confirmed "
        "prohibited correction is a CRITICAL finding; a guess is a "
        "false positive that wastes reviewer time."
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
        "**ABSENCE FIRST.** Most BPCR pages have NO attachments. If you "
        "see no labels, stickers, or pasted printouts, the correct "
        "answer is ``compliant`` with evidence \"no physical "
        "attachments visible on this page\" — do NOT speculate that "
        "attachments are missing.\n\n"
        "When attachments ARE visible, classify each:\n"
        "1. Affixed labels or stickers (chromatogram printouts, balance tickets)\n"
        "2. Pasted documents or printouts\n"
        "3. Loose or partially detached attachments — visible adhesive "
        "residue, torn corners, or peeling edges. Empty space alone is "
        "NOT evidence of detachment.\n\n"
        "**Do NOT report \"missing attachment\" unless there is direct "
        "visual evidence**: an attachment-reference label/number "
        "(e.g. \"Attachment 1\") pointing at a clearly empty space "
        "with adhesive residue or a marked outline. Without that "
        "specific evidence, an empty area is just an empty area — "
        "report ``compliant`` with \"no missing-attachment evidence "
        "observed.\""
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
        "Analyze this page image for physical document quality AND "
        "scan-process legibility.\n\n"
        "**ABSENCE FIRST.** A scanned document page in normal "
        "production condition is the COMMON case. Default to "
        "``compliant`` with \"no quality issues observed\" unless "
        "you can identify a specific defect that genuinely impairs "
        "GMP-critical data legibility.\n\n"
        "These are NOT quality defects: aged paper colour, slight "
        "off-white background tone, scanner-introduced shadows at "
        "page edges, JPEG compression artifacts, mild printer-toner "
        "variation, repeating template watermarks, and "
        "page-numbering separators. Do not flag them.\n\n"
        "Genuine PHYSICAL defects that warrant a finding:\n"
        "1. SMUDGES or stains that **obscure data text or signatures** "
        "   (cosmetic marks on margins are not flagged)\n"
        "2. FADING such that handwritten or critical printed entries "
        "   are no longer readable\n"
        "3. WATER DAMAGE — warping, ink bleeding through critical fields, "
        "   visible discoloration tied to data\n"
        "4. TEARS or physical damage that crosses data regions\n"
        "5. PENCIL marks on data fields (non-permanent annotations)\n\n"
        "Genuine SCAN-PROCESS defects that ALSO warrant a finding "
        "(added 2026-05-12 after Akhilesh hit a reactor-operations "
        "checklist whose middle rows were entirely blacked out by "
        "the scanner's threshold):\n"
        "6. SOLID BLACK BANDS / BLOCKS covering ≥ 1 row of a data "
        "   table — symptom of scanner over-thresholding or a "
        "   shadow misread by the binarization filter. Whole rows "
        "   render as featureless dark rectangles instead of text.\n"
        "7. EXCESSIVE DARKENING such that whole columns of text "
        "   collapse into solid black or near-black blobs — the "
        "   underlying ink IS there but the scan settings destroyed "
        "   the text/background contrast.\n"
        "8. OVER-COMPRESSED / BLURRED regions where individual "
        "   characters are no longer separable, specifically on "
        "   data cells (not margins or watermarks).\n"
        "9. MISSING PAGE CONTENT — visible blank rectangles or "
        "   cut-off margins on a page that clearly should have "
        "   carried data (e.g. only the top half of a checklist "
        "   table is visible, the bottom half is white).\n\n"
        "Report what you observe with bbox-approximate locations "
        "when possible (e.g. \"rows 3-5 of the checklist table "
        "are fully blacked out, page mid-section\"). When the "
        "defect is mid-page only and the surrounding content is "
        "readable, status is ``non_compliant`` (the data is "
        "lost). When the entire page is degraded, status is "
        "``non_compliant`` with severity major.\n\n"
        "If your confidence that a defect impairs critical data is "
        "below 0.7, return ``uncertain`` rather than ``non_compliant``."
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
        "**ABSENCE FIRST.** If the page has no checkboxes or checklist "
        "items at all, return ``compliant`` with \"no checkbox items "
        "on this page\".\n\n"
        "When checkboxes ARE present, do NOT attempt an exact total "
        "count — VLMs are unreliable at counting many small UI "
        "elements. Instead, report a coarse bucket per category:\n"
        "  ``none`` (0 items in this state)\n"
        "  ``few`` (a small minority — roughly <25%)\n"
        "  ``mixed`` (a substantial portion in this state)\n"
        "  ``most`` (clearly the majority)\n"
        "  ``all`` (every visible checkbox in this state)\n\n"
        "Categories: checked (tick/X/filled), unchecked (empty box), "
        "marked N/A. Only flag non_compliant when the rule's pass "
        "criteria explicitly require a fully-checked checklist and "
        "the bucket for ``unchecked`` is ``few`` or higher AND the "
        "unchecked items aren't marked N/A."
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
        "**ABSENCE FIRST.** Temporary annotations on a properly "
        "controlled BPCR are RARE. Default to ``compliant`` with "
        "\"no temporary annotations visible\" unless you see "
        "specific, unambiguous evidence.\n\n"
        "These are NOT temporary annotations and must NOT be flagged: "
        "official template watermarks (\"CONTROLLED COPY\", QA stamps, "
        "company logos), printer registration marks at page edges, "
        "page-number references, scanner artifacts, repeating header "
        "elements that appear on every page of the document.\n\n"
        "Genuine temporary annotations that warrant a finding:\n"
        "1. A YELLOW / PINK / BLUE STICKY NOTE — visibly affixed to "
        "   the page with adhesive, often with a different paper "
        "   colour and a slight shadow\n"
        "2. PENCIL handwriting on a data field (clearly graphite, "
        "   not ink)\n"
        "3. \"DRAFT\" watermark covering the page diagonally — but "
        "   only if it is NOT part of the document template (real "
        "   approved templates do not carry a DRAFT watermark)\n"
        "4. Hand-affixed paper labels with non-template content\n\n"
        "When the evidence is ambiguous (a faint mark, an indistinct "
        "watermark, a pale stamp), return ``uncertain``. A confirmed "
        "temporary annotation IS non-compliant; an ambiguous mark "
        "called non-compliant is a false positive that wastes "
        "reviewer time."
    ),
}

_VISION_SYSTEM_PROMPT = (
    "You are a pharmaceutical compliance visual inspector specializing in "
    "GMP (Good Manufacturing Practice) document review. You analyze page "
    "images from pharmaceutical batch production records, logbooks, and "
    "controlled documents.\n\n"
    "CRITICAL GUIDELINES:\n"
    "1. Base your assessment ONLY on what is visually present in the image. "
    "Do not infer history, intent, or context that isn't visible.\n"
    "2. Be precise about locations — describe as top/middle/bottom, left/center/right.\n"
    "3. Distinguish between genuine compliance issues and normal document features.\n"
    "4. For each rule, provide a clear status: compliant, non_compliant, "
    "not_applicable, or uncertain.\n\n"
    "ABSENCE-OF-VIOLATION DEFAULT (most important rule):\n"
    "When a rule asks about a specific defect or violation (corrections, "
    "missing signatures, prohibited annotations, ink-colour issues, etc.) "
    "and you do NOT see clear visual evidence of that defect, the correct "
    "status is ``compliant`` — NOT ``non_compliant``. The absence of a "
    "violation is the compliant state. ``non_compliant`` requires positive "
    "visual evidence of an actual violation, not ambiguity, not a "
    "speculative read, and not the lack of certainty about a feature's "
    "presence. ``not_applicable`` is appropriate when the rule's subject "
    "doesn't appear on the page at all (e.g. an ink-colour rule on a "
    "page with no handwritten entries).\n\n"
    "CONFIDENCE → STATUS MAPPING (use this exactly):\n"
    "- ≥0.85 confidence in a visible violation → ``non_compliant`` with "
    "the cited evidence.\n"
    "- 0.60-0.85 confidence in a visible violation → ``uncertain`` with the "
    "evidence cited; the downstream arbitrator reconciles with OCR signal.\n"
    "- <0.60 confidence in a violation, OR no violation observed → "
    "``compliant`` with evidence \"no <defect> observed on this page\". "
    "Do NOT emit a low-confidence ``non_compliant`` — that produces false "
    "positives that erode reviewer trust.\n\n"
    "5. Confidence numerals should reflect your certainty: 0.9+ for clear "
    "visual evidence, 0.6-0.8 for moderate clarity, <0.6 for ambiguous.\n"
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

        # Pre-flight: short-circuit colour-dependent checks on a B&W
        # scan. The VLM was emitting false positives like "red ink
        # detected" on grayscale-only pages because the VC-INK-COLOR
        # prompt forced a categorical answer with no clean
        # "grayscale" exit. Determining colour-presence is a cheap
        # deterministic image-stat operation; the VLM never sees the
        # image for those checks unless real chroma is present.
        gated_eval_ids: set[str] = set()
        gated_evals: list[RuleEvaluation] = []
        if not image_has_meaningful_colour(page_image):
            for rule in batch.rules:
                if any(
                    vc in _COLOUR_DEPENDENT_CHECKS for vc in rule.visual_checks
                ):
                    gated_eval_ids.add(rule.id)
                    gated_evals.append(RuleEvaluation(
                        rule_id=rule.id,
                        status="not_applicable",
                        confidence=1.0,
                        reasoning=(
                            "Page is a grayscale scan (no chromatic information "
                            "detected); ink colour cannot be determined from "
                            "this rendering. Re-scan in colour or supply a "
                            "colour-bearing source if ink-colour evaluation is "
                            "required for this document."
                        ),
                        evidence="Image saturation pre-check classified the page as B&W.",
                    ))

        # If every rule in the batch was gated out, skip the VLM call
        # entirely — saves cost and latency on a known-no-info input.
        remaining = [r for r in batch.rules if r.id not in gated_eval_ids]
        if not remaining:
            logger.info(
                "Vision batch %s page %d: all rules gated out by grayscale "
                "pre-check; skipping VLM call",
                batch.batch_id, page_num,
            )
            return batch.batch_id, page_num, RuleBatchResult(
                evaluations=gated_evals,
            )

        # Build the prompt only over the non-gated rules so the VLM
        # isn't asked about checks it can't honestly evaluate.
        residual_batch = (
            RuleBatch(
                batch_id=batch.batch_id,
                category=batch.category,
                agent=batch.agent,
                rules=remaining,
            )
            if gated_eval_ids
            else batch
        )

        prompt = _build_vision_prompt(residual_batch.rules, page_num)
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

                # Evaluate the residual (non-gated) rules normally,
                # then prepend the gated evaluations so the caller
                # gets one entry per rule in the original batch.
                bid, pn, residual_result = self._to_batch_result(
                    residual_batch, page_num, result,
                )
                if gated_evals:
                    residual_result = RuleBatchResult(
                        evaluations=gated_evals + list(residual_result.evaluations),
                    )
                return bid, pn, residual_result

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
        # On retry exhaustion: surface error evaluations for the
        # residual rules and keep the gated ``not_applicable``
        # evaluations untouched (they're deterministic and unrelated
        # to the VLM's failure).
        evals = list(gated_evals) + [
            RuleEvaluation(
                rule_id=r.id,
                status="error",
                description=f"Vision evaluation failed: {last_exc}",
            )
            for r in remaining
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
