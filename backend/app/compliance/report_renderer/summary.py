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

import re

from app.compliance.models import ComplianceFinding, RuleResult
from app.compliance.report_renderer.page_formatter import format_pages

_MAX_REASONING_PER_FINDING: int = 800
"""Cap on per-finding reasoning length when concatenating across
multiple findings. 800 chars accommodates synthesised 2-4 sentence
cross-page narratives while keeping the PDF cell readable."""

_PAGE_CITATION_RE = re.compile(r"\bPAGE[S]?\s*:\s*\d", re.IGNORECASE)
"""Compiled regex that matches PAGE:N or PAGES:N inline citations
produced by all three evaluators (text, vision, agentic)."""


def _has_page_citation(text: str) -> bool:
    """Return True when *text* contains at least one PAGE:N inline citation."""
    return bool(_PAGE_CITATION_RE.search(text))


def summarise_compliant(rule_result: RuleResult) -> str:
    """Produce the Detailed-Evidence cell text for a compliant row.

    v1 is deterministic — no LLM call.

    Agentic rules produce a high-level ``reasoning`` (conclusion without
    page citations) and a citation-rich ``evidence``. When reasoning is
    present but lacks PAGE:N inline citations, we append the evidence so
    the PDF cell shows the cross-page support alongside the conclusion.
    """

    pages = format_pages(rule_result.page_numbers)
    page_count = len(rule_result.page_numbers)

    reasoning = (rule_result.reasoning or "").strip()
    evidence = (rule_result.evidence or "").strip()

    if reasoning and len(reasoning) > 40:
        if not _has_page_citation(reasoning) and evidence and len(evidence) > 10:
            # Reasoning is a high-level conclusion (e.g. agentic audit); evidence
            # carries the PAGE:N citations. Combine so both appear in the cell.
            return f"{reasoning}\n\n{evidence}"
        return reasoning

    # Fall back to evidence when reasoning is absent or too short.
    if evidence and len(evidence) > 10:
        return evidence

    # Boilerplate fallback when neither field has content.
    if page_count == 0:
        return (
            "This rule was evaluated for the document and found "
            "compliant. No exceptions surfaced during the review."
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
        return (
            "The rule was flagged but the evaluator did not record "
            "specific findings. Review the underlying evaluation log."
        )

    return " ".join(parts)
