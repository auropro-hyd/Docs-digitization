"""Unit tests for report_renderer/summary.py.

Test design: each test covers one observable behaviour of
summarise_compliant() or concat_finding_evidence(). Tests are
named after the behaviour, not the implementation.
"""

from __future__ import annotations

from app.compliance.models import ComplianceFinding, RuleResult
from app.compliance.report_renderer.summary import (
    _has_page_citation,
    concat_finding_evidence,
    summarise_compliant,
)


# ── _has_page_citation ────────────────────────────────────────────────────────


def test_has_page_citation_matches_page_colon_n() -> None:
    assert _has_page_citation("The entry on PAGE:3 is signed.")


def test_has_page_citation_matches_pages_colon_n() -> None:
    assert _has_page_citation("See PAGES:3-4 for the batch record.")


def test_has_page_citation_matches_case_insensitively() -> None:
    assert _has_page_citation("page:12 shows the material quantity.")


def test_has_page_citation_returns_false_for_plain_text() -> None:
    assert not _has_page_citation(
        "All dispensed materials are accounted for in executed manufacturing steps."
    )


def test_has_page_citation_returns_false_for_page_without_colon_digit() -> None:
    # "page" without ":N" should not match (e.g. "on the next page")
    assert not _has_page_citation("See the relevant page for details.")


# ── summarise_compliant — agentic combine path ────────────────────────────────


def _agentic_rule(
    reasoning: str = "",
    evidence: str = "",
    page_numbers: list[int] | None = None,
) -> RuleResult:
    return RuleResult(
        rule_id="CHE-DOC1",
        rule_text="Are all dispensed materials accounted for?",
        rule_category="document_completeness",
        agent="checklist",
        status="compliant",
        page_numbers=page_numbers or [],
        reasoning=reasoning,
        evidence=evidence,
    )


def test_agentic_compliant_combines_reasoning_and_evidence_when_reasoning_lacks_citation() -> None:
    """Core agentic-audit fix: reasoning has no PAGE:N but evidence does.

    The rendered cell must include BOTH the conclusion and the citations.
    """
    rule = _agentic_rule(
        reasoning="All dispensed materials are accounted for with matching signatures.",
        evidence="The BPCR on PAGES:3-4 lists raw materials. Manufacturing steps on PAGES:6-21 show executed steps.",
    )
    result = summarise_compliant(rule)

    assert "All dispensed materials" in result
    assert "PAGES:3-4" in result
    assert "PAGES:6-21" in result


def test_agentic_compliant_reasoning_with_citation_returned_as_is() -> None:
    """When reasoning already has PAGE:N citations, do not append evidence."""
    rule = _agentic_rule(
        reasoning="PAGE:3: All material records are signed and match batch numbers.",
        evidence="The BPCR on PAGE:3 lists raw materials.",
    )
    result = summarise_compliant(rule)

    assert result == "PAGE:3: All material records are signed and match batch numbers."
    # evidence should NOT be appended — reasoning is self-contained
    assert result.count("PAGE:3") == 1


def test_agentic_compliant_falls_back_to_evidence_when_reasoning_absent() -> None:
    """When reasoning is empty, return evidence directly."""
    rule = _agentic_rule(
        reasoning="",
        evidence="The BPCR on PAGES:3-4 records batch details.",
    )
    result = summarise_compliant(rule)

    assert result == "The BPCR on PAGES:3-4 records batch details."


def test_agentic_compliant_boilerplate_when_both_fields_empty() -> None:
    """With no data at all, return the page-count boilerplate."""
    rule = _agentic_rule(reasoning="", evidence="", page_numbers=[])
    result = summarise_compliant(rule)

    assert "compliant" in result.lower()


def test_non_agentic_compliant_returns_reasoning_with_citation() -> None:
    """Standard (non-agentic) rules that already have PAGE:N in reasoning
    are returned unchanged — no evidence appended."""
    rule = _agentic_rule(
        reasoning="PAGE:15: The 'Done By' and 'Checked By' fields are both signed.",
        evidence="",
    )
    result = summarise_compliant(rule)

    assert result.startswith("PAGE:15:")
    assert "\n\n" not in result


def test_short_reasoning_falls_through_to_evidence() -> None:
    """Reasoning <= 40 chars is treated as absent — evidence is shown."""
    rule = _agentic_rule(
        reasoning="OK.",
        evidence="The BPCR on PAGE:5 is signed.",
    )
    result = summarise_compliant(rule)

    assert result == "The BPCR on PAGE:5 is signed."


# ── concat_finding_evidence ───────────────────────────────────────────────────


def _finding(rule_id: str = "R1", reasoning: str = "", evidence: str = "") -> ComplianceFinding:
    return ComplianceFinding(
        finding_id="fnd-1",
        rule_id=rule_id,
        rule_text="Rule text",
        rule_category="checklist",
        agent="checklist",
        severity="major",
        status="non_compliant",
        page_numbers=[5],
        reasoning=reasoning,
        evidence=evidence,
        hitl_status="auto_approved",
    )


def test_concat_prefers_reasoning_over_evidence() -> None:
    f = _finding(reasoning="PAGE:5: Signature missing.", evidence="The Done By field is blank.")
    assert concat_finding_evidence([f]) == "PAGE:5: Signature missing."


def test_concat_falls_back_to_evidence_when_reasoning_absent() -> None:
    f = _finding(reasoning="", evidence="The Done By field on PAGE:5 is blank.")
    assert concat_finding_evidence([f]) == "The Done By field on PAGE:5 is blank."


def test_concat_returns_empty_string_for_no_findings() -> None:
    assert concat_finding_evidence([]) == ""


def test_concat_caps_long_reasoning() -> None:
    long_text = "X" * 900
    f = _finding(reasoning=long_text)
    result = concat_finding_evidence([f])
    assert len(result) <= 800
    assert result.endswith("…")
