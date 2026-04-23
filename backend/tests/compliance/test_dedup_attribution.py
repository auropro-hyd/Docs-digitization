"""FR-014 / FR-015 / SC-005: dedup modes + attribution invariant."""

from __future__ import annotations

from app.compliance.evaluator import _deduplicate_findings, resync_agent_totals
from app.compliance.models import AgentReport, ComplianceFinding


def _mk_finding(agent: str, rule_id: str, *, fid: str, severity: str = "major") -> ComplianceFinding:
    return ComplianceFinding(
        finding_id=fid,
        rule_id=rule_id,
        rule_text="x",
        rule_category="cat",
        rule_category_display="Cat",
        agent=agent,
        severity=severity,
        status="non_compliant",
        confidence=0.9,
        page_numbers=[1],
        reasoning="r",
        evidence="e",
        description="d",
        recommendation="",
        applicability_trace=[],
        hitl_status="auto_approved",
    )


def test_per_agent_dedups_by_rule_only() -> None:
    findings = [
        _mk_finding("alcoa", "r1", fid="alcoa-1"),
        _mk_finding("alcoa", "r1", fid="alcoa-2"),  # same agent same rule = dup
        _mk_finding("alcoa", "r2", fid="alcoa-3"),
    ]
    out = _deduplicate_findings(findings, mode="per_agent")
    assert {f.rule_id for f in out} == {"r1", "r2"}
    assert len(out) == 2


def test_cross_agent_preserve_keeps_both_agents() -> None:
    findings = [
        _mk_finding("alcoa", "r1", fid="alcoa-1"),
        _mk_finding("gmp", "r1", fid="gmp-1"),
    ]
    out = _deduplicate_findings(findings, mode="cross_agent_preserve")
    agents = sorted(f.agent for f in out)
    assert agents == ["alcoa", "gmp"]
    # And same-agent duplicates still collapse within the preserve mode.
    more = [
        _mk_finding("alcoa", "r1", fid="alcoa-1a"),
        _mk_finding("alcoa", "r1", fid="alcoa-1b"),
        _mk_finding("gmp", "r1", fid="gmp-1"),
    ]
    out2 = _deduplicate_findings(more, mode="cross_agent_preserve")
    assert len(out2) == 2  # (alcoa, r1) + (gmp, r1)


def test_cross_agent_collapse_wins_first_seen_and_counts_metric() -> None:
    from app.observability.metrics import COMPLIANCE_DEDUP_MERGES

    before = 0.0
    for sample in COMPLIANCE_DEDUP_MERGES.collect()[0].samples:
        before = sample.value if sample.labels.get("mode") == "cross_agent_collapse" else before

    findings = [
        _mk_finding("alcoa", "r1", fid="alcoa-1"),
        _mk_finding("gmp", "r1", fid="gmp-1"),  # dropped; alcoa first
    ]
    out = _deduplicate_findings(findings, mode="cross_agent_collapse")
    assert len(out) == 1
    assert out[0].agent == "alcoa"

    after = 0.0
    for sample in COMPLIANCE_DEDUP_MERGES.collect()[0].samples:
        if sample.labels.get("mode") == "cross_agent_collapse":
            after = sample.value
    assert after >= before + 1


def test_attribution_invariant_in_preserve_mode() -> None:
    """SC-005: sum(ar.total_findings) == |global-findings-with-agent|."""

    findings = [
        _mk_finding("alcoa", "r1", fid="alcoa-1"),
        _mk_finding("gmp", "r1", fid="gmp-1"),
        _mk_finding("alcoa", "r2", fid="alcoa-2"),
        _mk_finding("checklist", "r3", fid="checklist-1"),
    ]
    out = _deduplicate_findings(findings, mode="cross_agent_preserve")

    def _ar(agent: str, n: int) -> AgentReport:
        return AgentReport(
            agent=agent,
            agent_display=agent,
            score=100.0,
            model_score=100.0,
            total_rules=5,
            total_findings=n,
            severity_counts={},
            category_scores=[],
            findings=[],
            all_evaluations=[],
            pages_reviewed=[],
        )

    reports = [_ar("alcoa", 2), _ar("gmp", 1), _ar("checklist", 1)]
    total_from_tabs = sum(ar.total_findings for ar in reports)
    total_from_global = sum(1 for f in out if f.agent is not None)
    assert total_from_tabs == total_from_global


def test_resync_agent_totals_fixes_collapse_drift() -> None:
    """Legacy collapse mode: resync rebalances ar.total_findings."""

    findings = [
        _mk_finding("alcoa", "r1", fid="alcoa-1"),
        _mk_finding("gmp", "r1", fid="gmp-1"),  # dropped by collapse
    ]
    global_after = _deduplicate_findings(findings, mode="cross_agent_collapse")
    ar_alcoa = AgentReport(
        agent="alcoa", agent_display="alcoa", score=100.0, model_score=100.0,
        total_rules=5, total_findings=1, severity_counts={},
        category_scores=[], findings=[], all_evaluations=[], pages_reviewed=[],
    )
    ar_gmp = AgentReport(
        agent="gmp", agent_display="gmp", score=100.0, model_score=100.0,
        total_rules=5, total_findings=1, severity_counts={},
        category_scores=[], findings=[], all_evaluations=[], pages_reviewed=[],
    )
    resync_agent_totals([ar_alcoa, ar_gmp], global_after)
    # gmp's finding was dropped in collapse, so total_findings must go to 0.
    assert ar_alcoa.total_findings == 1
    assert ar_gmp.total_findings == 0
