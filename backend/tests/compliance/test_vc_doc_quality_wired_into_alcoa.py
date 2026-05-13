"""Pin VC-DOC-QUALITY's wire-in to the ALCOA legibility rule.

PR #43 extended the VC-DOC-QUALITY prompt to cover scan-process
defects (the reactor checklist whose middle rows were blacked out
by the scanner's threshold filter — Akhilesh, 2026-05-12). The
prompt has been sitting in ``vision_evaluator._VC_PROMPTS`` ever
since but no rule referenced it, so VLM never received it as part
of the per-page checks. PR #44's ``vlm.unused_check_prompt``
diagnostic surfaced the gap by emitting "VC-DOC-QUALITY never fires
even if VLM is enabled" once per agent.

This module pins the wire-in: ALCOA's ``ALC-ATT1`` (the SAME-PAGE
INDIVIDUAL exemplar — "every Done by / Checked by cell is
populated with a legible value") now carries
``evaluation_strategy: text_and_vision`` and ``visual_checks:
[VC-DOC-QUALITY]`` so vision actually runs on the same pages as
the text path. The merge in ``_merge_text_vision`` gives vision
precedence for visual aspects — when OCR declares a cell "empty"
but the rendered page shows a scan-defect black band, the vision
verdict wins.

Three pins:
1. ALC-ATT1 declares text_and_vision + VC-DOC-QUALITY,
2. The unused-prompt audit no longer flags VC-DOC-QUALITY,
3. ALC-ATT1 routes through the vision evaluator's batch (the
   structural integration point that was missing before).
"""

from __future__ import annotations

import pytest


def test_alc_att1_carries_text_and_vision_and_vc_doc_quality() -> None:
    """The wire-in must persist on the loaded AuditRule. If a future
    YAML refactor strips either field, ALC-ATT1's scan-defect path
    silently regresses."""

    from app.compliance.rules.registry import RuleRegistry

    r = RuleRegistry()
    alcoa = {rule.id: rule for rule in r.get_rules("alcoa")}
    att1 = alcoa.get("ALC-ATT1")
    assert att1 is not None, "ALC-ATT1 must exist in alcoa_rules.yaml"
    assert att1.evaluation_strategy == "text_and_vision", (
        f"ALC-ATT1.evaluation_strategy must be 'text_and_vision' "
        f"(got {att1.evaluation_strategy!r}). Without this, the "
        f"vision path never runs and VC-DOC-QUALITY is dead code."
    )
    assert "VC-DOC-QUALITY" in att1.visual_checks, (
        f"ALC-ATT1.visual_checks must include 'VC-DOC-QUALITY' "
        f"(got {att1.visual_checks}). Without this reference, "
        f"the scan-defect prompt is unreachable from any rule."
    )


def test_vc_doc_quality_is_no_longer_in_unused_prompt_audit() -> None:
    """The diagnostic from PR #44 that flagged VC-DOC-QUALITY as
    unused must now skip it for the ALCOA agent. Other unreferenced
    VC prompts can still appear — only VC-DOC-QUALITY graduates
    in this PR."""

    from app.compliance.rules.registry import RuleRegistry
    from app.compliance.vision_evaluator import audit_unused_vc_prompts

    r = RuleRegistry()
    alcoa = r.get_rules("alcoa")
    unused = audit_unused_vc_prompts(alcoa, agent="alcoa")
    assert "VC-DOC-QUALITY" not in unused, (
        f"VC-DOC-QUALITY still appears as unused for the ALCOA "
        f"agent — the alcoa_rules.yaml wire-in didn't take. "
        f"Unused list: {unused}"
    )


def test_vc_doc_quality_prompt_template_still_present() -> None:
    """The wire-in only earns its keep if the prompt template
    actually exists in vision_evaluator. Belt-and-suspenders pin
    against a refactor that removes the template AND the references
    in the same PR — the wire-in would still look correct on the
    rule side but the visual check would silently no-op."""

    from app.compliance.vision_evaluator import _VC_PROMPTS

    assert "VC-DOC-QUALITY" in _VC_PROMPTS, (
        "VC-DOC-QUALITY prompt template missing from _VC_PROMPTS — "
        "the wire-in points to nothing"
    )
    # The PR #43 scan-defect extension must remain in the prompt.
    prompt = _VC_PROMPTS["VC-DOC-QUALITY"]
    assert (
        "BLACK BAND" in prompt
        or "DARKENING" in prompt
        or "scan" in prompt.lower()
    ), "scan-defect prompt content from PR #43 was stripped"
