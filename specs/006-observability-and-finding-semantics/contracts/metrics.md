# Contract — Metrics Catalogue

**Satisfies**: FR-004 (metrics endpoint), FR-005 (named set), NFR-002 (cardinality).

Every metric emitted by the service is defined exactly once in `app/observability/metrics.py` and listed here. Domain code imports by name; it does not construct metrics at call sites. A unit test (`test_metrics_catalogue.py`) asserts the registered set equals the set listed below and that every label is on the cardinality whitelist.

All names follow Prometheus conventions (`<subsystem>_<noun>_<unit>`). Durations are seconds. Sizes are bytes.

---

## Cardinality whitelist

The authoritative list lives in `research.md §R5`. It is imported as a frozen set `ALLOWED_LABELS` by `app/observability/metrics.py`. Any label name not in that set fails the `test_no_banned_labels` drift test at build time.

Summary (full rationale per label in research.md):

```
method, route, status_class,
agent, stage, scope,
status, severity, hitl_status,
model, direction, purpose,
kind, mode,
gate_status, action, reason_type,
endpoint
```

Forbidden (unbounded): `doc_id`, `run_id`, `finding_id`, `rule_id`, `actor_id`, `user_email`, `package_id`, any free-form identifier. Document-scoped or run-scoped context belongs in logs and traces, never in metric labels.

---

## HTTP transport

### `http_requests_total` (counter)

Requests received and answered, labelled by route template (not concrete path) and response status class.

- Labels: `method`, `route`, `status_class`
- Values: `method` ∈ `{GET, POST, DELETE, WebSocket, …}`; `route` = FastAPI path template (e.g. `POST /api/compliance/{doc_id}/run`); `status_class` ∈ `{1xx, 2xx, 3xx, 4xx, 5xx}`
- Cardinality budget: ≤ 5 methods × ~100 routes × 5 classes = ~2500

### `http_request_duration_seconds` (histogram)

End-to-end handler duration per route.

- Labels: `method`, `route`
- Buckets: `[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60]`

### `http_request_body_bytes` (histogram)

Request body size (for upload endpoints).

- Labels: `route`
- Buckets: `[1_024, 10_240, 102_400, 1_048_576, 10_485_760, 104_857_600, 1_073_741_824]`

---

## Compliance pipeline

### `compliance_runs_total` (counter)

Compliance runs started, by terminal status.

- Labels: `status` ∈ `{ok, failed, cancelled}`

### `compliance_run_duration_seconds` (histogram)

End-to-end compliance run duration.

- Labels: `status`
- Buckets: `[1, 5, 10, 30, 60, 120, 300, 600, 1800]`

### `compliance_agent_duration_seconds` (histogram)

Per-agent evaluation duration within a run.

- Labels: `agent`, `status`
- Buckets: same as run duration

### `compliance_findings_total` (counter)

Compliance findings emitted, joined across the full `(agent, status, severity, hitl_status)` grid.

- Labels: `agent`, `status`, `severity`, `hitl_status`
- Cardinality budget: 5 × 5 × 4 × 6 = 600

### `compliance_dedup_merges_total` (counter)

Cross-agent dedup events (when the report is built in `cross_agent_collapse` mode).

- Labels: `mode` ∈ `{per_agent, cross_agent_preserve, cross_agent_collapse}`

### `compliance_rule_evaluations_total` (counter)

Rule evaluations attempted. `status` here is the *rule* outcome, not a finding's HITL state.

- Labels: `agent`, `status` ∈ `{compliant, non_compliant, uncertain, not_applicable, error}`

### `compliance_rule_evaluation_duration_seconds` (histogram)

Single-rule evaluation duration.

- Labels: `agent`
- Buckets: `[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30]`

---

## BMR pipeline

### `bmr_runs_total` (counter)

BMR runs by terminal status.

- Labels: `status` ∈ `{completed, failed, awaiting_legibility_review}`

### `bmr_run_duration_seconds` (histogram)

End-to-end BMR run duration.

- Labels: `status`
- Buckets: `[1, 5, 10, 30, 60, 120, 300, 600, 1800]`

### `bmr_stage_duration_seconds` (histogram)

Per-stage duration.

- Labels: `stage` ∈ `{ingest, legibility_and_classification, extraction, compliance, report}`
- Buckets: `[0.1, 0.5, 1, 5, 10, 30, 60, 300]`

### `bmr_rules_evaluated_total` (counter)

BMR rules evaluated, by scope.

- Labels: `status` ∈ `{pass, open, indeterminate, unevaluated}`, `scope` ∈ `{same_page, cross_document, page_aggregate, checklist_synthesis}`

*(Note: `scope` is bounded and allowed as a label; it's 4 values.)*

### `bmr_runs_in_flight` (gauge)

Currently-executing BMR runs.

- No labels.

---

## HITL

### `hitl_resolutions_total` (counter)

Resolutions recorded.

- Labels: `action` ∈ `{CONFIRM, DISMISS, CORRECT}`, `reason_type` ∈ `{OCR_MISREAD, ACCEPTABLE_VARIANCE, DUPLICATE_FINDING, OTHER, NONE}`

### `hitl_corrections_total` (counter)

Correction workflows started.

- Labels: `status` ∈ `{pending, applied, failed}`

### `hitl_export_attempts_total` (counter)

Export attempts by gate state.

- Labels: `gate_status` ∈ `{READY, BLOCKED_BY_PENDING_FINDINGS, BLOCKED_BY_STALE_RESOLUTIONS}`

### `hitl_revisions_total` (counter)

Audit-report revisions produced.

- No labels.

---

## LLM layer

### `llm_calls_total` (counter)

LLM calls, by model and purpose.

- Labels: `model`, `purpose` ∈ `{evaluator, orchestrator, summary, vision, discover_rules, other}`

### `llm_call_duration_seconds` (histogram)

LLM round-trip duration.

- Labels: `model`, `purpose`
- Buckets: `[0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120]`

### `llm_tokens_total` (counter)

Tokens consumed.

- Labels: `model`, `direction` ∈ `{prompt, completion}`

### `llm_call_failures_total` (counter)

LLM call failures, by reason.

- Labels: `model`, `kind` ∈ `{timeout, rate_limit, server_error, invalid_response, auth, other}`

---

## Errors

### `errors_total` (counter)

Unhandled exceptions, by exception class and route.

- Labels: `route`, `kind`
- `kind` is the exception class name, folded to a whitelist (common classes kept, exotic ones fold to `Exception`).
- Cardinality budget: ≤ 100 routes × ≤ 50 kinds = 5000

---

## Health

### `healthchecks_total` (counter)

Health endpoint hits. Sanity counter; useful to verify probes are firing.

- Labels: `endpoint` ∈ `{health, ready}`, `status` ∈ `{ok, failed}`

---

## Process

### `process_start_time_seconds` (gauge, built-in)

Provided by `prometheus_client` default collectors. Useful for uptime alerts. No custom config needed.

---

## Layout

```python
# app/observability/metrics.py
from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry

REGISTRY = CollectorRegistry(auto_describe=True)

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
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
    registry=REGISTRY,
)
# ... (full list per this document)

__all__ = [name for name, _ in __dict__.items() if name.isupper()]
```

A test imports the module and asserts `__all__` equals the expected set exactly — accidentally adding a metric without updating this document fails the test.
