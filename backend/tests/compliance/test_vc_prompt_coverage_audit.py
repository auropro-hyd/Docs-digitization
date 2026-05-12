"""Pin the unused-VC-prompt diagnostic.

Run e5e35ffc-… (2026-05-12) had VC-DOC-QUALITY's scan-defect prompt
extension from PR #43 but no rule in the active ``alcoa_rules.yaml``
carried ``visual_checks: [VC-DOC-QUALITY]`` — so the prompt was
unreachable and the check never fired. This module pins the
diagnostic that catches the gap by static comparison rather than
waiting for "the VLM never returned a VC-DOC-QUALITY result" to be
noticed downstream.
"""

from __future__ import annotations

import pytest

from app.compliance.rules.registry import AuditRule
from app.compliance.vision_evaluator import (
    _VC_PROMPTS,
    audit_unused_vc_prompts,
)


def _rule(rid: str, visual_checks: list[str] | None = None) -> AuditRule:
    return AuditRule(
        id=rid,
        number=1,
        category="alcoa",
        category_display="ALCOA",
        agent="alcoa",
        text="dummy",
        visual_checks=visual_checks or [],
    )


def test_unused_vc_prompt_surfaces_in_audit() -> None:
    """When a VC prompt has no rule reference, the audit returns it
    in the unused list. VC-DOC-QUALITY is the prod example."""

    # Rule set that references only ONE of the defined VC prompts.
    # Pick any defined VC ID — we just need at least one to ensure
    # the audit isn't returning the full defined set in error.
    some_referenced = next(iter(_VC_PROMPTS))
    rules = [_rule("ALC-ATT1", visual_checks=[some_referenced])]

    unused = audit_unused_vc_prompts(rules, agent="alcoa")

    # Every other VC prompt is unused.
    expected_unused = set(_VC_PROMPTS.keys()) - {some_referenced}
    assert set(unused) == expected_unused
    # VC-DOC-QUALITY specifically must surface — the prod symptom.
    assert "VC-DOC-QUALITY" in unused or "VC-DOC-QUALITY" not in _VC_PROMPTS


def test_full_coverage_yields_empty_unused_list() -> None:
    """When every defined VC prompt is referenced by at least one
    rule, the audit returns an empty list (no diagnostic noise)."""

    rules = [_rule(f"R-{i}", visual_checks=[vc_id])
             for i, vc_id in enumerate(_VC_PROMPTS)]

    unused = audit_unused_vc_prompts(rules, agent="alcoa")

    assert unused == []


def test_empty_rule_set_flags_every_defined_prompt() -> None:
    """A degenerate case — agent with no visual_checks-tagged rules
    at all. Every defined VC prompt is unused; the audit surfaces
    all of them so the operator sees the full coverage gap."""

    unused = audit_unused_vc_prompts([], agent="cross_page")

    assert set(unused) == set(_VC_PROMPTS.keys())
