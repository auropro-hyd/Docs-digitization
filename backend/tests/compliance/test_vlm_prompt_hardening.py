"""Pin the FP-prevention guarantees PR #26 added to the VLM prompts.

These are snapshot-style tests on prompt content. They don't exercise
the VLM (that would require a live Gemini call to be useful) — they
just assert that the FP-prevention affordances we audited in are
still there. A future contributor who tightens the prompts is free
to do so; one who *removes* the absence-default or the
uncertain-on-low-confidence escape will get a clear test failure
pointing at the regression.

The audit context:

  Akhilesh hit a VLM false positive on 2026-04-30: the VLM declared
  red ink present on a B&W scan because the original VC-INK-COLOR
  prompt forced a categorical answer with no \"I can't tell\" exit.
  PR #24 added a deterministic grayscale gate to fix that one
  symptom. PR #26 audited the remaining 13 VC-* prompts for the
  same pattern (closed list / no absence escape / high-stakes
  amplifier / counterfactual ask / counting task) and hardened the
  six worst.
"""

from __future__ import annotations

import pytest

from app.compliance.vision_evaluator import (
    _VC_PROMPTS,
    _VISION_SYSTEM_PROMPT,
)


@pytest.mark.parametrize("vc_id", [
    "VC-STRIKE",
    "VC-CORRECTION",
    "VC-ATTACHMENT",
    "VC-CHECKBOX",
    "VC-DOC-QUALITY",
    "VC-STICKY-NOTE",
])
def test_high_fp_risk_prompts_carry_an_absence_first_clause(vc_id: str) -> None:
    """Every high-FP-risk prompt must explicitly tell the VLM what to
    do when its target is NOT present. Without this, the original
    \"For each X visible / Look for evidence of Y\" framing primes
    the VLM to invent findings on pages where nothing is wrong.
    """

    prompt = _VC_PROMPTS[vc_id]
    assert "ABSENCE FIRST" in prompt, (
        f"{vc_id} no longer carries the absence-first directive — "
        "this is the primary defence against false positives. If you "
        "intentionally restructured the prompt, update this test "
        "and the system prompt to match."
    )
    assert "compliant" in prompt.lower(), (
        f"{vc_id} must explicitly route the absence case to ``compliant``"
    )


def test_vc_correction_explicitly_warns_against_common_false_positives() -> None:
    """White-out / erasure detection is technically hard for VLMs.
    The hardened prompt names the specific things that get
    misclassified (light printer toner, JPEG artifacts, naturally
    lighter form fields) so the model can disambiguate.
    """

    prompt = _VC_PROMPTS["VC-CORRECTION"]
    for phrase in ("printer toner", "form fields", "JPEG", "uncertain"):
        assert phrase.lower() in prompt.lower(), (
            f"VC-CORRECTION lost the warning about {phrase!r} — that "
            "phrase is the disambiguator that prevents the VLM from "
            "calling lighter regions \"white-out\""
        )


def test_vc_attachment_blocks_speculative_missing_attachment_findings() -> None:
    """The original prompt asked the VLM to flag \"missing attachments
    (empty spaces where items were previously affixed)\" — a
    counterfactual the VLM can't reliably detect. The hardened
    prompt limits \"missing\" to direct visual evidence
    (adhesive residue, attachment-reference labels) and explicitly
    forbids speculation from empty space.
    """

    prompt = _VC_PROMPTS["VC-ATTACHMENT"]
    assert "Do NOT report" in prompt and "missing attachment" in prompt
    assert "adhesive residue" in prompt or "marked outline" in prompt, (
        "VC-ATTACHMENT must require concrete evidence (adhesive "
        "residue / outlined empty space / attachment-reference label) "
        "before flagging a missing attachment"
    )


def test_vc_checkbox_no_longer_asks_for_total_counts() -> None:
    """VLMs are bad at counting. The hardened prompt moves to coarse
    buckets the VLM can judge reliably.
    """

    prompt = _VC_PROMPTS["VC-CHECKBOX"]
    # Should NOT ask for a total count.
    assert "total number" not in prompt.lower(), (
        "VC-CHECKBOX still asks for an exact count — VLMs are "
        "unreliable at counting many small UI elements; use bucketed "
        "estimates instead"
    )
    # Should explain bucketed reporting.
    assert "few" in prompt and "most" in prompt, (
        "VC-CHECKBOX must specify the bucket vocabulary "
        "(none/few/mixed/most/all) the VLM should use"
    )


def test_system_prompt_carries_absence_default_and_confidence_mapping() -> None:
    """Two cross-cutting rules in the system prompt protect every
    rule evaluation from the FP cascade:

    1. **Absence default** — when no violation is observed, the
       answer is ``compliant``, not ``non_compliant``. The opposite
       was the dominant FP source pre-PR #26.
    2. **Confidence-to-status mapping** — sub-0.6 confidence
       findings must downgrade to ``uncertain``, not stay as
       ``non_compliant``. The downstream ``llm_arbitrated`` strategy
       on the 008/009 branches reconciles the OCR signal at that
       point.
    """

    sp = _VISION_SYSTEM_PROMPT.lower()
    assert "absence-of-violation default" in sp or "absence of a violation" in sp, (
        "system prompt must carry the absence-default rule — the "
        "single most important guarantee against FP cascade"
    )
    assert "non_compliant" in sp and "uncertain" in sp and "compliant" in sp, (
        "system prompt must enumerate the status options"
    )
    # The confidence mapping mentions both ≥0.85 (firm) and <0.60
    # (downgrade). These are the boundaries that prevent the FP
    # cascade described in the module docstring.
    assert "0.85" in sp or "0.60" in sp, (
        "system prompt must carry the confidence→status thresholds "
        "explicitly — without them the VLM emits low-confidence "
        "non_compliant findings that the downstream arbitrator has "
        "to filter back out"
    )


def test_vc_ink_color_still_carries_grayscale_guard() -> None:
    """Belt-and-braces: PR #24's prompt-level GRAYSCALE escape hatch
    must remain even though the deterministic pre-flight gate also
    short-circuits this case. The two layers protect against
    different failure modes (env-var thresholds drifting; the
    pre-flight check failing open on a corrupt image)."""

    prompt = _VC_PROMPTS["VC-INK-COLOR"]
    assert "GRAYSCALE GUARD" in prompt or "grayscale scan" in prompt.lower()
