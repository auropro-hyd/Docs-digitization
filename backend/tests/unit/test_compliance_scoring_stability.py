"""Scoring stability benchmark tests for compliance reporting."""

from __future__ import annotations

from copy import deepcopy

from app.api.routes.compliance import _recompute_review_adjusted_scores


def _base_report() -> dict:
    return {
        "overall_score": 83.2,
        "score_methodology": {
            "deduction_weights": {"critical": 10, "major": 5, "minor": 2, "observation": 1},
        },
        "agent_reports": [
            {
                "agent": "alcoa",
                "score": 81.0,
                "total_rules": 82,
                "findings": [
                    {"finding_id": "a-1", "rule_id": "ALC-ACC42", "severity": "major", "hitl_status": "needs_review"},
                    {"finding_id": "a-2", "rule_id": "ALC-COM53", "severity": "critical", "hitl_status": "user_rejected"},
                    {"finding_id": "a-3", "rule_id": "ALC-LEG19", "severity": "minor", "hitl_status": "user_modified"},
                ],
            },
            {
                "agent": "gmp",
                "score": 87.0,
                "total_rules": 31,
                "findings": [
                    {"finding_id": "g-1", "rule_id": "GMP-DEV9", "severity": "major", "hitl_status": "auto_approved"},
                ],
            },
        ],
    }


def test_review_adjusted_score_is_deterministic_across_runs():
    report = _base_report()
    _recompute_review_adjusted_scores(report)
    first = (
        report["review_adjusted_score"],
        report["agent_reports"][0]["review_adjusted_score"],
        report["agent_reports"][1]["review_adjusted_score"],
    )

    # Re-run on same payload to simulate repeated status/read calls.
    _recompute_review_adjusted_scores(report)
    second = (
        report["review_adjusted_score"],
        report["agent_reports"][0]["review_adjusted_score"],
        report["agent_reports"][1]["review_adjusted_score"],
    )

    assert first == second


def test_review_adjusted_score_has_zero_order_variance_for_findings():
    report_a = _base_report()
    report_b = deepcopy(report_a)

    # Reverse ordering to ensure score is independent of finding order.
    report_b["agent_reports"][0]["findings"] = list(reversed(report_b["agent_reports"][0]["findings"]))
    report_b["agent_reports"][1]["findings"] = list(reversed(report_b["agent_reports"][1]["findings"]))

    _recompute_review_adjusted_scores(report_a)
    _recompute_review_adjusted_scores(report_b)

    assert report_a["review_adjusted_score"] == report_b["review_adjusted_score"]
    assert report_a["agent_reports"][0]["review_adjusted_score"] == report_b["agent_reports"][0]["review_adjusted_score"]
    assert report_a["agent_reports"][1]["review_adjusted_score"] == report_b["agent_reports"][1]["review_adjusted_score"]
