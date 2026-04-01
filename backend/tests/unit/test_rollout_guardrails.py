from app.core.services.rollout_guardrails import evaluate_rollback_guardrails, select_canary_variant


def test_select_canary_variant_is_deterministic():
    a = select_canary_variant("doc-123", canary_enabled=True, canary_percent=20)
    b = select_canary_variant("doc-123", canary_enabled=True, canary_percent=20)
    assert a == b
    assert a["variant"] in {"baseline", "routed_query"}


def test_evaluate_rollback_guardrails_flags_regressions():
    out = evaluate_rollback_guardrails(
        quality_f1_delta=-0.05,
        latency_ms_delta=200.0,
        cost_usd_delta=0.03,
        min_quality_f1_delta=-0.01,
        max_latency_ms_delta=100.0,
        max_cost_usd_delta=0.02,
    )
    assert out["should_rollback"] is True
    assert len(out["reasons"]) >= 1
