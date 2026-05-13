"""Pin the ``ComplianceReport`` → ``ReportDocument`` pure transform.

This is the source of truth for both the renderer and the
``/report-rows`` endpoint. The whole client-aligned shape is
derived here.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.compliance.models import (
    AgentReport,
    ComplianceFinding,
    ComplianceReport,
    RuleResult,
)
from app.compliance.report_renderer.builder import build_report_document


_NOW = datetime(2026, 5, 14, 18, 30, 0, tzinfo=timezone.utc)


def _make_report(
    *,
    agent_reports: list[AgentReport],
    doc_id: str = "doc-X",
    filename: str = "2538104192-EHSII03.pdf",
    document_type: str = "batch_record",
) -> ComplianceReport:
    return ComplianceReport(
        report_id="rpt-1",
        doc_id=doc_id,
        filename=filename,
        total_pages=104,
        document_type=document_type,
        generated_at=_NOW,
        agent_reports=agent_reports,
    )


def _rule(rule_id: str, status: str, pages: list[int] = (), agent: str = "checklist",
          text: str = "Rule text", reasoning: str = "", category: str = "checklist") -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        rule_text=text,
        rule_category=category,
        agent=agent,
        status=status,
        page_numbers=list(pages),
        reasoning=reasoning,
    )


def _finding(rule_id: str, status: str, pages: list[int] = (), agent: str = "checklist",
             reasoning: str = "", recommendation: str = "",
             hitl: str = "auto_approved") -> ComplianceFinding:
    return ComplianceFinding(
        finding_id=f"{rule_id}-fnd",
        rule_id=rule_id,
        rule_text="Rule text",
        rule_category="checklist",
        agent=agent,
        severity="major",
        status=status,
        page_numbers=list(pages),
        reasoning=reasoning,
        recommendation=recommendation,
        hitl_status=hitl,
    )


# ── Row construction per status bucket ──────────────────────────


def test_compliant_row_has_empty_pages_and_summary_and_not_applicable_mitigation() -> None:
    """FR-003 + FR-004 + FR-006a: compliant rows leave the pages
    cell empty, summarise evidence across pages, and use literal
    "Not Applicable" in the mitigation column."""

    rule = _rule("CHE-DOC1", "compliant",
                 pages=[103],
                 text="Are all attachments enclosed with the BPCR?",
                 reasoning="The BPCR review check list confirms all attachments present.")
    agent = AgentReport(agent="checklist", agent_display="Checklist",
                        all_evaluations=[rule])
    report = _make_report(agent_reports=[agent])

    doc = build_report_document(report, now=_NOW)

    assert len(doc.rows) == 1
    row = doc.rows[0]
    assert row.compliance_kind == "compliant"
    assert row.compliance_label == "Compliant"
    assert row.evidence_pages == "", "compliant pages cell MUST be empty"
    assert "attachments present" in row.detailed_evidence.lower() or "BPCR" in row.detailed_evidence
    assert row.mitigation == "Not Applicable"


def test_non_compliant_row_has_pages_evidence_and_mitigation() -> None:
    """FR-005 + FR-006b: non-compliant rows show page references,
    finding evidence, and actionable mitigation text from the
    rule-author's recommendation."""

    rule = _rule("CHE-BPC11", "non_compliant",
                 pages=[70],
                 text="Are all in process checks performed and compiled?",
                 reasoning="Water content check at Step 67 missing.")
    finding = _finding("CHE-BPC11", "non_compliant",
                       pages=[70],
                       reasoning="The water content check is missing from Step 67.",
                       recommendation="An investigation must be initiated to determine why the water content check at Step 67 was not documented.")
    agent = AgentReport(agent="checklist", agent_display="Checklist",
                        all_evaluations=[rule], findings=[finding])
    report = _make_report(agent_reports=[agent])

    doc = build_report_document(report, now=_NOW)

    row = doc.rows[0]
    assert row.compliance_kind == "action_required"
    assert row.compliance_label == "Action Required"
    assert row.evidence_pages == "PAGE:70"
    assert "water content" in row.detailed_evidence.lower()
    assert "investigation" in row.mitigation.lower()


def test_uncertain_row_buckets_as_needs_attention() -> None:
    rule = _rule("CHE-BPC13", "uncertain",
                 pages=[18, 20, 21, 32],
                 text="Is output meeting standard yield range?",
                 reasoning="Conflicting weights reported.")
    finding = _finding("CHE-BPC13", "uncertain",
                       pages=[18, 20, 21, 32],
                       reasoning="The packing slip on PAGE 13 shows 978.50 kg; the dispense log on PAGE 20 shows 977.32 kg.",
                       recommendation="A formal investigation is required to reconcile the conflicting weight values.")
    agent = AgentReport(agent="checklist", all_evaluations=[rule], findings=[finding])
    report = _make_report(agent_reports=[agent])

    doc = build_report_document(report, now=_NOW)

    row = doc.rows[0]
    assert row.compliance_kind == "needs_attention"
    assert row.compliance_label == "Needs Attention"
    assert row.evidence_pages == "PAGE:18, 20, 21, 32"
    assert "investigation" in row.mitigation.lower()


def test_not_applicable_row_is_excluded() -> None:
    """FR-014: rules with status='not_applicable' don't appear in
    the report. They surface in the ``excluded_not_applicable_count``
    stat instead."""

    rule_skip = _rule("CHE-SKIP", "not_applicable", text="Skipped rule")
    rule_ok = _rule("CHE-OK", "compliant", text="OK rule", pages=[1])
    agent = AgentReport(agent="checklist",
                        all_evaluations=[rule_skip, rule_ok])
    report = _make_report(agent_reports=[agent])

    doc = build_report_document(report, now=_NOW)

    rule_ids = [r.rule_id for r in doc.rows]
    assert "CHE-SKIP" not in rule_ids
    assert "CHE-OK" in rule_ids
    assert doc.stats.excluded_not_applicable_count == 1


# ── HITL override flips non-compliant → compliant ──────────────


def test_user_approved_finding_flips_row_to_compliant() -> None:
    """FR-020: when an operator approves a non-compliant finding
    (e.g. confirms the OCR misread), the row's badge becomes
    Compliant — the operator's verdict is the source of truth."""

    rule = _rule("CHE-1", "non_compliant", pages=[5],
                 text="A questionable rule")
    finding = _finding("CHE-1", "non_compliant", pages=[5],
                       reasoning="False positive — OCR misread.",
                       recommendation="No action needed.",
                       hitl="user_approved")
    agent = AgentReport(agent="checklist", all_evaluations=[rule], findings=[finding])
    report = _make_report(agent_reports=[agent])

    doc = build_report_document(report, now=_NOW)

    row = doc.rows[0]
    assert row.compliance_kind == "compliant"
    assert row.evidence_pages == "", "approved row treated as compliant — no pages"
    assert row.mitigation == "Not Applicable"


# ── Row ordering: action items first ───────────────────────────


def test_rows_sorted_action_items_first() -> None:
    """The rule table puts action-required rows at the top so the
    operator's eye lands on failures, then needs-attention, then
    compliant. Pin the sort order."""

    rule_compliant = _rule("CHE-A", "compliant", pages=[1])
    rule_uncertain = _rule("CHE-B", "uncertain", pages=[2])
    rule_failure = _rule("CHE-C", "non_compliant", pages=[3])
    agent = AgentReport(
        agent="checklist",
        all_evaluations=[rule_compliant, rule_uncertain, rule_failure],
        findings=[
            _finding("CHE-B", "uncertain", pages=[2]),
            _finding("CHE-C", "non_compliant", pages=[3]),
        ],
    )
    report = _make_report(agent_reports=[agent])

    doc = build_report_document(report, now=_NOW)

    rule_ids = [r.rule_id for r in doc.rows]
    assert rule_ids == ["CHE-C", "CHE-B", "CHE-A"]


# ── Agent display names + multi-agent rules ────────────────────


def test_agent_display_names_match_frontend() -> None:
    rule = _rule("ALC-1", "compliant", agent="alcoa", pages=[1])
    agent = AgentReport(agent="alcoa", all_evaluations=[rule])
    report = _make_report(agent_reports=[agent])

    doc = build_report_document(report, now=_NOW)

    assert doc.rows[0].agent == "ALCOA+"


def test_multi_agent_rule_produces_separate_rows() -> None:
    """FR-017: same rule_id evaluated by two agents (cross-doc
    reconciliation case) produces two rows with agent chips."""

    rule_a = _rule("REC-MAT1", "compliant", agent="reconciliation", pages=[1])
    rule_b = _rule("REC-MAT1", "non_compliant", agent="checklist", pages=[2])
    agents = [
        AgentReport(agent="reconciliation", all_evaluations=[rule_a]),
        AgentReport(agent="checklist", all_evaluations=[rule_b],
                    findings=[_finding("REC-MAT1", "non_compliant", pages=[2])]),
    ]
    report = _make_report(agent_reports=agents)

    doc = build_report_document(report, now=_NOW)

    assert len(doc.rows) == 2
    assert {r.agent for r in doc.rows} == {"Cross-Page", "Checklist"}


# ── Stats counters ─────────────────────────────────────────────


def test_stats_count_each_bucket() -> None:
    rules = [
        _rule("R1", "compliant", pages=[1]),
        _rule("R2", "compliant", pages=[2]),
        _rule("R3", "non_compliant", pages=[3]),
        _rule("R4", "uncertain", pages=[4]),
        _rule("R5", "not_applicable"),
    ]
    findings = [
        _finding("R3", "non_compliant", pages=[3]),
        _finding("R4", "uncertain", pages=[4]),
    ]
    agent = AgentReport(agent="checklist", all_evaluations=rules, findings=findings)
    report = _make_report(agent_reports=[agent])

    doc = build_report_document(report, now=_NOW)

    assert doc.stats.row_count == 4
    assert doc.stats.compliant_count == 2
    assert doc.stats.action_required_count == 1
    assert doc.stats.needs_attention_count == 1
    assert doc.stats.excluded_not_applicable_count == 1


# ── Header + footer ─────────────────────────────────────────────


def test_header_carries_product_name_default() -> None:
    rule = _rule("R1", "compliant", pages=[1])
    agent = AgentReport(agent="checklist", all_evaluations=[rule])
    report = _make_report(agent_reports=[agent])

    doc = build_report_document(report, now=_NOW)

    assert doc.header.product_name == "BMR Compliance Intelligence Suite"
    assert doc.header.is_draft is True


def test_header_product_name_overridable_for_reskin() -> None:
    rule = _rule("R1", "compliant", pages=[1])
    agent = AgentReport(agent="checklist", all_evaluations=[rule])
    report = _make_report(agent_reports=[agent])

    doc = build_report_document(
        report, now=_NOW, product_name="Custom Brand Name",
    )

    assert doc.header.product_name == "Custom Brand Name"


def test_header_title_from_document_type() -> None:
    rule = _rule("R1", "compliant", pages=[1])
    agent = AgentReport(agent="checklist", all_evaluations=[rule])
    report = _make_report(agent_reports=[agent], document_type="batch_record")

    doc = build_report_document(report, now=_NOW)

    assert doc.header.title == "BMR Compliance Review"


def test_footer_disclaimer_carries_operator_and_timestamp() -> None:
    """The footer string must include both the operator name and the
    formatted timestamp. Renderer drops it into every page band."""

    rule = _rule("R1", "compliant", pages=[1])
    agent = AgentReport(agent="checklist", all_evaluations=[rule])
    report = _make_report(agent_reports=[agent])

    doc = build_report_document(report, now=_NOW, operator="Manoj Sankad")

    assert "Manoj Sankad" in doc.footer.disclaimer
    assert "05/14/2026" in doc.footer.disclaimer
    assert "Pharmix AI" in doc.footer.disclaimer, (
        "engine brand 'Pharmix AI' stays in the disclaimer per the "
        "two-brand-layer model (masthead = product name; "
        "footer = engine)"
    )


# ── Agent filter ────────────────────────────────────────────────


def test_agent_filter_scopes_rows() -> None:
    """When the route passes ``?agent=gmp`` the builder only emits
    rows from that agent."""

    agents = [
        AgentReport(agent="alcoa", all_evaluations=[_rule("ALC-1", "compliant", agent="alcoa", pages=[1])]),
        AgentReport(agent="gmp", all_evaluations=[_rule("GMP-1", "compliant", agent="gmp", pages=[2])]),
    ]
    report = _make_report(agent_reports=agents)

    doc = build_report_document(report, now=_NOW, agent_filter="gmp")

    assert len(doc.rows) == 1
    assert doc.rows[0].rule_id == "GMP-1"
