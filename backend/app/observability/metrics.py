"""Named Prometheus metrics — single source of truth.

Every metric listed in
``specs/006-observability-and-finding-semantics/contracts/metrics.md``
is registered here exactly once. Domain code imports by name and never
constructs its own metrics at call sites (``test_metrics_catalogue.py``
asserts this).

Label whitelist is frozen in :data:`ALLOWED_LABELS` — a metric using a
label not in the set fails :func:`validate_registry` at import time.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

# Authoritative label whitelist (research.md R5).
ALLOWED_LABELS: frozenset[str] = frozenset(
    {
        "method",
        "route",
        "status_class",
        "agent",
        "stage",
        "scope",
        "status",
        "severity",
        "hitl_status",
        "model",
        "direction",
        "purpose",
        "kind",
        "mode",
        "gate_status",
        "action",
        "reason_type",
        "endpoint",
    }
)

REGISTRY = CollectorRegistry(auto_describe=True)


# ── HTTP transport ────────────────────────────────────────────────────────────

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests by route + status class.",
    ("method", "route", "status_class"),
    registry=REGISTRY,
)

HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "End-to-end handler duration per route.",
    ("method", "route"),
    buckets=(
        0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5,
        1, 2.5, 5, 10, 30, 60,
    ),
    registry=REGISTRY,
)

HTTP_REQUEST_BYTES = Histogram(
    "http_request_body_bytes",
    "Request body size (upload routes only).",
    ("route",),
    buckets=(
        1_024, 10_240, 102_400, 1_048_576,
        10_485_760, 104_857_600, 1_073_741_824,
    ),
    registry=REGISTRY,
)


# ── Compliance pipeline ───────────────────────────────────────────────────────

COMPLIANCE_RUNS = Counter(
    "compliance_runs_total",
    "Compliance runs by terminal status.",
    ("status",),
    registry=REGISTRY,
)

COMPLIANCE_RUN_DURATION = Histogram(
    "compliance_run_duration_seconds",
    "End-to-end compliance run duration.",
    ("status",),
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1_800),
    registry=REGISTRY,
)

COMPLIANCE_AGENT_DURATION = Histogram(
    "compliance_agent_duration_seconds",
    "Per-agent evaluation duration within a run.",
    ("agent", "status"),
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1_800),
    registry=REGISTRY,
)

COMPLIANCE_FINDINGS = Counter(
    "compliance_findings_total",
    "Compliance findings emitted by (agent, status, severity, hitl_status).",
    ("agent", "status", "severity", "hitl_status"),
    registry=REGISTRY,
)

COMPLIANCE_DEDUP_MERGES = Counter(
    "compliance_dedup_merges_total",
    "Cross-agent dedup collapses.",
    ("mode",),
    registry=REGISTRY,
)

COMPLIANCE_RULE_EVALUATIONS = Counter(
    "compliance_rule_evaluations_total",
    "Rule evaluations attempted.",
    ("agent", "status"),
    registry=REGISTRY,
)

COMPLIANCE_RULE_DURATION = Histogram(
    "compliance_rule_evaluation_duration_seconds",
    "Single-rule evaluation duration.",
    ("agent",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    registry=REGISTRY,
)


# ── BMR pipeline ──────────────────────────────────────────────────────────────

BMR_RUNS = Counter(
    "bmr_runs_total",
    "BMR runs by terminal status.",
    ("status",),
    registry=REGISTRY,
)

BMR_RUN_DURATION = Histogram(
    "bmr_run_duration_seconds",
    "End-to-end BMR run duration.",
    ("status",),
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1_800),
    registry=REGISTRY,
)

BMR_STAGE_DURATION = Histogram(
    "bmr_stage_duration_seconds",
    "Per-stage duration within a BMR run.",
    ("stage",),
    buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 300),
    registry=REGISTRY,
)

BMR_RULES_EVALUATED = Counter(
    "bmr_rules_evaluated_total",
    "BMR rules evaluated, by (status, scope).",
    ("status", "scope"),
    registry=REGISTRY,
)

BMR_RUNS_IN_FLIGHT = Gauge(
    "bmr_runs_in_flight",
    "Currently-executing BMR runs.",
    registry=REGISTRY,
)


# ── HITL ──────────────────────────────────────────────────────────────────────

HITL_RESOLUTIONS = Counter(
    "hitl_resolutions_total",
    "Resolutions recorded.",
    ("action", "reason_type"),
    registry=REGISTRY,
)

HITL_CORRECTIONS = Counter(
    "hitl_corrections_total",
    "Correction workflows by status.",
    ("status",),
    registry=REGISTRY,
)

HITL_EXPORT_ATTEMPTS = Counter(
    "hitl_export_attempts_total",
    "Export attempts by gate state.",
    ("gate_status",),
    registry=REGISTRY,
)

HITL_REVISIONS = Counter(
    "hitl_revisions_total",
    "Audit-report revisions produced.",
    (),
    registry=REGISTRY,
)


# ── LLM ───────────────────────────────────────────────────────────────────────

LLM_CALLS = Counter(
    "llm_calls_total",
    "LLM calls by model + purpose.",
    ("model", "purpose"),
    registry=REGISTRY,
)

LLM_CALL_DURATION = Histogram(
    "llm_call_duration_seconds",
    "LLM round-trip duration.",
    ("model", "purpose"),
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
    registry=REGISTRY,
)

LLM_TOKENS = Counter(
    "llm_tokens_total",
    "Tokens consumed by model + direction.",
    ("model", "direction"),
    registry=REGISTRY,
)

LLM_CALL_FAILURES = Counter(
    "llm_call_failures_total",
    "LLM call failures by kind.",
    ("model", "kind"),
    registry=REGISTRY,
)


# ── Errors, logging, health ──────────────────────────────────────────────────

ERRORS = Counter(
    "errors_total",
    "Unhandled exceptions by route + exception kind.",
    ("route", "kind"),
    registry=REGISTRY,
)

LOG_REDACTIONS = Counter(
    "log_redactions_total",
    "Redactions performed by the logging pipeline.",
    ("kind",),
    registry=REGISTRY,
)

HEALTHCHECKS = Counter(
    "healthchecks_total",
    "Health endpoint hits.",
    ("endpoint", "status"),
    registry=REGISTRY,
)


# ── Catalogue + drift + test isolation ───────────────────────────────────────

_REGISTERED = (
    HTTP_REQUESTS,
    HTTP_DURATION,
    HTTP_REQUEST_BYTES,
    COMPLIANCE_RUNS,
    COMPLIANCE_RUN_DURATION,
    COMPLIANCE_AGENT_DURATION,
    COMPLIANCE_FINDINGS,
    COMPLIANCE_DEDUP_MERGES,
    COMPLIANCE_RULE_EVALUATIONS,
    COMPLIANCE_RULE_DURATION,
    BMR_RUNS,
    BMR_RUN_DURATION,
    BMR_STAGE_DURATION,
    BMR_RULES_EVALUATED,
    BMR_RUNS_IN_FLIGHT,
    HITL_RESOLUTIONS,
    HITL_CORRECTIONS,
    HITL_EXPORT_ATTEMPTS,
    HITL_REVISIONS,
    LLM_CALLS,
    LLM_CALL_DURATION,
    LLM_TOKENS,
    LLM_CALL_FAILURES,
    ERRORS,
    LOG_REDACTIONS,
    HEALTHCHECKS,
)


def _labels_of(metric: Any) -> Iterable[str]:
    # prometheus_client stores label names on ``_labelnames`` for all metric types.
    return tuple(getattr(metric, "_labelnames", ()) or ())


def _metric_name(metric: Any) -> str:
    """Return the full exposition name (e.g. ``http_requests_total``).

    ``prometheus_client`` stores the name minus the type-specific suffix
    (``_total`` for counters). We reconstruct the exposition-facing name
    so the catalogue in ``contracts/metrics.md`` reads 1:1.
    """

    stored = getattr(metric, "_name", "") or ""
    type_name = type(metric).__name__
    if type_name == "Counter" and not stored.endswith("_total"):
        return f"{stored}_total"
    return stored


def catalogue() -> list[tuple[str, tuple[str, ...]]]:
    """Return ``[(metric_name, labels), ...]`` for the registered set.

    Used by :func:`validate_registry` and the drift test in
    ``tests/observability/test_metrics_catalogue.py``.
    """

    return [(_metric_name(m), tuple(_labels_of(m))) for m in _REGISTERED]


def validate_registry() -> None:
    """Enforce the label whitelist. Called at import time (see below)."""

    offenders: list[tuple[str, str]] = []
    for m in _REGISTERED:
        for label in _labels_of(m):
            if label not in ALLOWED_LABELS:
                offenders.append((_metric_name(m), label))
    if offenders:
        pretty = ", ".join(f"{name}.{label}" for name, label in offenders)
        raise RuntimeError(
            f"metric(s) registered with non-whitelisted labels: {pretty}. "
            f"Add the label to ALLOWED_LABELS with a cardinality justification "
            f"in research.md §R5, or drop it from the metric."
        )


def reset_for_tests() -> None:
    """Clear every counter/histogram/gauge so tests don't observe each other.

    ``prometheus_client`` has no public reset API; we re-enter private state
    per metric. This is the pattern used throughout their own test suite.
    """

    import contextlib

    for m in _REGISTERED:
        with contextlib.suppress(AttributeError):
            m._metrics.clear()  # type: ignore[attr-defined]
        with contextlib.suppress(AttributeError):
            # Unlabelled singletons use ``_value`` directly.
            m._value.set(0)  # type: ignore[attr-defined]


# Enforce the whitelist at import time. A new metric that violates it fails
# loud and early, before tests run.
validate_registry()


__all__ = [
    "ALLOWED_LABELS",
    "BMR_RUNS",
    "BMR_RUN_DURATION",
    "BMR_RULES_EVALUATED",
    "BMR_RUNS_IN_FLIGHT",
    "BMR_STAGE_DURATION",
    "COMPLIANCE_AGENT_DURATION",
    "COMPLIANCE_DEDUP_MERGES",
    "COMPLIANCE_FINDINGS",
    "COMPLIANCE_RULE_DURATION",
    "COMPLIANCE_RULE_EVALUATIONS",
    "COMPLIANCE_RUNS",
    "COMPLIANCE_RUN_DURATION",
    "ERRORS",
    "HEALTHCHECKS",
    "HITL_CORRECTIONS",
    "HITL_EXPORT_ATTEMPTS",
    "HITL_RESOLUTIONS",
    "HITL_REVISIONS",
    "HTTP_DURATION",
    "HTTP_REQUESTS",
    "HTTP_REQUEST_BYTES",
    "LLM_CALLS",
    "LLM_CALL_DURATION",
    "LLM_CALL_FAILURES",
    "LLM_TOKENS",
    "LOG_REDACTIONS",
    "REGISTRY",
    "catalogue",
    "reset_for_tests",
    "validate_registry",
]
