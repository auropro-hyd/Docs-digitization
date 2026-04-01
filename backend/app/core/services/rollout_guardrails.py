"""Canary rollout selection and rollback guardrail evaluation."""

from __future__ import annotations

import hashlib
from typing import Any


def select_canary_variant(doc_id: str, *, canary_enabled: bool, canary_percent: int) -> dict[str, Any]:
    if not canary_enabled:
        return {"variant": "baseline", "bucket": 100, "enabled": False}
    pct = max(0, min(100, int(canary_percent)))
    digest = hashlib.sha1(str(doc_id).encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 100
    variant = "routed_query" if bucket < pct else "baseline"
    return {"variant": variant, "bucket": bucket, "enabled": True}


def evaluate_rollback_guardrails(
    *,
    quality_f1_delta: float,
    latency_ms_delta: float,
    cost_usd_delta: float,
    min_quality_f1_delta: float,
    max_latency_ms_delta: float,
    max_cost_usd_delta: float,
) -> dict[str, Any]:
    reasons: list[str] = []
    if quality_f1_delta < min_quality_f1_delta:
        reasons.append("quality_regression")
    if latency_ms_delta > max_latency_ms_delta:
        reasons.append("latency_budget_exceeded")
    if cost_usd_delta > max_cost_usd_delta:
        reasons.append("cost_budget_exceeded")
    return {
        "should_rollback": bool(reasons),
        "reasons": reasons,
        "metrics": {
            "quality_f1_delta": quality_f1_delta,
            "latency_ms_delta": latency_ms_delta,
            "cost_usd_delta": cost_usd_delta,
        },
    }
