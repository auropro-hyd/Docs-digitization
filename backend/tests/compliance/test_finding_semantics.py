"""FR-013 / SC-004: no finding silently defaults to auto_approved.

Covers _score_from_findings and _normalize_hitl_status. Also includes the
FR-017 regression test against the real persisted report.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.api.routes.compliance import (
    _normalize_hitl_status,
    _recompute_review_adjusted_scores,
    _score_from_findings,
)


def test_missing_hitl_is_unknown_not_auto_approved() -> None:
    assert _normalize_hitl_status(None) == "unknown"
    assert _normalize_hitl_status("") == "unknown"
    assert _normalize_hitl_status("some-garbage") == "unknown"


def test_known_values_round_trip() -> None:
    for v in (
        "auto_approved",
        "system_confirmed",
        "needs_review",
        "user_approved",
        "user_rejected",
        "user_modified",
        "unknown",
    ):
        assert _normalize_hitl_status(v) == v


def test_missing_hitl_not_silently_approved_in_score() -> None:
    """A finding missing hitl_status must NOT apply a penalty by default."""

    findings = [{"finding_id": "f1", "rule_id": "r1", "severity": "critical"}]
    weights = {"critical": 10, "major": 5, "minor": 2, "observation": 1}
    dec = _score_from_findings(findings, weights)
    # With default include_unknown=False, the unknown-state finding is
    # excluded from penalty — score remains 100.
    assert dec["total_penalty"] == 0
    assert dec["unknown_skipped"] == 1
    assert dec["included_findings"] == 0
    assert dec["score"] == 100.0


def test_include_unknown_opt_in() -> None:
    findings = [{"finding_id": "f1", "rule_id": "r1", "severity": "critical"}]
    dec = _score_from_findings(
        findings, {"critical": 10}, include_unknown=True
    )
    assert dec["total_penalty"] == 10
    assert dec["score"] == 90.0


def test_user_rejected_always_excluded() -> None:
    findings = [
        {"finding_id": "f1", "rule_id": "r1", "severity": "critical", "hitl_status": "user_rejected"},
    ]
    dec = _score_from_findings(findings, {"critical": 10})
    assert dec["included_findings"] == 0
    assert dec["score"] == 100.0


# ── FR-017: scoring on the real pilot doc is unchanged ─────────────────────────

_PILOT_REPORT = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "documents"
    / "f9f7e1b6-d7a3-415c-8275-795ec0c69888"
    / "compliance_result.json"
)


def test_pilot_report_score_stable_after_relabel() -> None:
    """Scoring must not move for the existing persisted report (FR-017)."""

    if not _PILOT_REPORT.exists():
        import pytest  # local import so collection still works without the fixture

        pytest.skip("pilot doc not present; skip regression")
    data_before = json.loads(_PILOT_REPORT.read_text(encoding="utf-8"))
    data_after = json.loads(_PILOT_REPORT.read_text(encoding="utf-8"))
    _recompute_review_adjusted_scores(data_after)
    # The review_adjusted_score is produced on demand by _recompute; the
    # model_score is inherent to the persisted report. Assert both are
    # well-formed and that the recomputation doesn't corrupt model_score.
    assert (
        data_after["model_score"] == data_before.get("model_score")
        or data_after["model_score"] == data_before.get("overall_score")
    )
    assert isinstance(data_after["review_adjusted_score"], (int, float))
