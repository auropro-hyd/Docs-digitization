"""Detailed-evidence text generation for the rule-centric report.

Two surfaces:

  * For compliant rows — generate a 2-3-sentence cross-page summary.
    Akhilesh's pointer: "evidence should be summary of overall
    evidence include the highlights from two or three page/document
    level evidence output (after parallel processing of all relevant
    pages applicable for that rule)".

    v1 uses a deterministic boilerplate. A future enhancement can
    add an ``AuditRule.summary_template`` field for rule-author-
    written summaries, or wire in an LLM call. The boilerplate
    keeps the MVP shippable without a per-rule LLM cost.

  * For non-compliant / uncertain rows — concatenate the finding's
    reasoning + evidence into a coherent paragraph. The findings
    were already authored by the per-page evaluator; we just need
    to lift their reasoning into the row.
"""

from __future__ import annotations

from app.compliance.models import ComplianceFinding, RuleResult
from app.compliance.report_renderer.page_formatter import format_pages

_MAX_REASONING_PER_FINDING: int = 400
"""Cap on per-finding reasoning length when concatenating across
multiple findings. Keeps the row visually readable in the rendered
PDF — 400 chars is roughly 4-5 lines of body text at the rule
table's column width."""


def summarise_compliant(rule_result: RuleResult) -> str:
    """Produce the Detailed-Evidence cell text for a compliant row.

    v1 is deterministic — no LLM call. The reasoning is the
    rule-author's authored text (when present) or a boilerplate
    cross-page acknowledgement.
    """

    pages = format_pages(rule_result.page_numbers)
    page_count = len(rule_result.page_numbers)

    # Prefer the rule's own reasoning if it has substantive content —
    # rule authors sometimes write "compliant" reasoning that
    # describes WHAT was checked. That's better than boilerplate.
    if rule_result.reasoning and len(rule_result.reasoning.strip()) > 40:
        # Keep the authored reasoning; let it stand on its own.
        return rule_result.reasoning.strip()

    # Boilerplate fallback. Page count + range gives the operator
    # enough signal that the rule actually ran broadly.
    if page_count == 0:
        return (
            f"This rule was evaluated for the document and found "
            f"compliant. No exceptions surfaced during the review."
        )

    category = rule_result.rule_category.replace("_", " ") or "compliance"
    if page_count == 1:
        return (
            f"Evaluated on a single page ({pages}). The page satisfied "
            f"the {category} criteria."
        )

    return (
        f"Evaluated across {page_count} page(s) ({pages}). "
        f"All evaluated pages satisfied the {category} criteria."
    )


def concat_finding_evidence(findings: list[ComplianceFinding]) -> str:
    """Produce the Detailed-Evidence cell text for a non-compliant /
    uncertain row.

    Prefers the finding's ``reasoning`` (the evaluator's
    explanation of WHY it failed); falls back to ``evidence`` (the
    quoted document snippet); falls back to ``description``. Caps
    each entry so very-long reasoning doesn't break the layout.
    """

    if not findings:
        return ""

    parts: list[str] = []
    for f in findings:
        text = (f.reasoning or f.evidence or f.description or "").strip()
        if not text:
            continue
        if len(text) > _MAX_REASONING_PER_FINDING:
            text = text[: _MAX_REASONING_PER_FINDING - 1].rstrip() + "…"
        parts.append(text)

    if not parts:
        # Every finding had empty reasoning/evidence/description —
        # rare, but possible. Surface SOMETHING so the cell isn't
        # blank.
        return (
            "The rule was flagged but the evaluator did not record "
            "specific findings. Review the underlying evaluation log."
        )

    return " ".join(parts)
