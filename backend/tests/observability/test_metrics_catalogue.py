"""FR-005 / NFR-002: catalogue matches spec and every label is whitelisted."""

from __future__ import annotations

from app.observability.metrics import ALLOWED_LABELS, catalogue, validate_registry

# Expected catalogue mirrors contracts/metrics.md one-to-one. If you add or
# rename a metric, update BOTH this set AND the contract doc.
EXPECTED = {
    "http_requests_total": ("method", "route", "status_class"),
    "http_request_duration_seconds": ("method", "route"),
    "http_request_body_bytes": ("route",),
    "compliance_runs_total": ("status",),
    "compliance_run_duration_seconds": ("status",),
    "compliance_agent_duration_seconds": ("agent", "status"),
    "compliance_findings_total": ("agent", "status", "severity", "hitl_status"),
    "compliance_dedup_merges_total": ("mode",),
    "compliance_rule_evaluations_total": ("agent", "status"),
    "compliance_rule_evaluation_duration_seconds": ("agent",),
    "bmr_runs_total": ("status",),
    "bmr_run_duration_seconds": ("status",),
    "bmr_stage_duration_seconds": ("stage",),
    "bmr_rules_evaluated_total": ("status", "scope"),
    "bmr_runs_in_flight": (),
    "hitl_resolutions_total": ("action", "reason_type"),
    "hitl_corrections_total": ("status",),
    "hitl_export_attempts_total": ("gate_status",),
    "hitl_revisions_total": (),
    "llm_calls_total": ("model", "purpose"),
    "llm_call_duration_seconds": ("model", "purpose"),
    "llm_tokens_total": ("model", "direction"),
    "llm_call_failures_total": ("model", "kind"),
    "errors_total": ("route", "kind"),
    "log_redactions_total": ("kind",),
    "healthchecks_total": ("endpoint", "status"),
}


def test_catalogue_matches_contract_exactly() -> None:
    actual = {name: tuple(labels) for name, labels in catalogue()}
    assert set(actual) == set(EXPECTED), (
        f"drift vs contracts/metrics.md — "
        f"missing={set(EXPECTED) - set(actual)} extra={set(actual) - set(EXPECTED)}"
    )
    for name, labels in EXPECTED.items():
        assert actual[name] == labels, (
            f"{name}: expected labels {labels}, got {actual[name]}"
        )


def test_no_banned_labels() -> None:
    # validate_registry runs at import; re-run here so the assertion message
    # is located on this test when someone breaks the whitelist.
    validate_registry()
    for _, labels in catalogue():
        for label in labels:
            assert label in ALLOWED_LABELS, (
                f"label {label!r} is not in ALLOWED_LABELS; add it to "
                f"research.md §R5 with a cardinality budget, or drop it."
            )


def test_banned_identifier_labels_rejected() -> None:
    for banned in ("doc_id", "run_id", "rule_id", "finding_id", "actor_id", "user_email"):
        assert banned not in ALLOWED_LABELS, (
            f"{banned!r} must stay out of ALLOWED_LABELS — unbounded cardinality."
        )
