# Research — Observability & Finding-Semantics

This document records the binding technical decisions for Spec 006. Each decision states the question, the options considered, the chosen option, and the rationale. Once merged, deviations from these decisions require a follow-up research entry.

---

## R1. Wire format for request correlation

**Question**: What header(s) should the backend accept on the way in and emit on the way out to correlate one request across processes, workers, async boundaries, and eventual tracing backends?

**Options**:

1. Custom header, e.g. `X-Request-Id: <uuid4>`. Simple. Compatible with existing FastAPI examples. Not interoperable with tracing vendors.
2. W3C Trace Context (`traceparent`, `tracestate`). OpenTelemetry native. Interoperable with Datadog, Honeycomb, Grafana Tempo, Jaeger. Header parsing is non-trivial but small.
3. Both: accept and emit `traceparent` (primary) plus `X-Request-Id` (human-readable echo of the hex trace id).

**Decision**: Option 3 — both.

**Rationale**:

- `traceparent` is the only header that will pay off the moment we wire an OTEL collector; rewriting every client and every log pipeline later to migrate from a custom header is an expensive no-op.
- `X-Request-Id` is the header every engineer is used to copy-pasting into `grep`, and every browser DevTools / curl user sees. Emitting both costs 2 bytes of response and eliminates friction.
- We do *not* introduce an extra `X-Correlation-Id` header — that's redundant with `X-Request-Id` and confuses documentation.

**Non-goals**: Parsing `tracestate` vendor segments. We echo `tracestate` unchanged on response and leave it otherwise alone.

---

## R2. Logging library

**Question**: How do we get structured, context-enriched, JSON-in-prod / dev-friendly-in-dev logs without rewriting every `logger.info()` call in the codebase?

**Options**:

1. Stdlib `logging` with a custom `Formatter` that reads `contextvars`. Minimal dependency. Verbose config. No typed event model.
2. `structlog` + stdlib bridge. Typed `event_dict`, rich processor chain (add context, redact, render). Compatible with existing `logging.getLogger(__name__)` calls via `structlog.stdlib.ProcessorFormatter`.
3. `loguru`. Terser API than structlog. Single-library ecosystem; no first-class `logging` bridge; less common in observability tooling.
4. OpenTelemetry Logs SDK. Correct long-term destination; heavy now (pulls exporters, batching, etc.) and overkill for v0.

**Decision**: Option 2 — `structlog` with a stdlib bridge.

**Rationale**:

- The codebase already has ~40 `logger = logging.getLogger(__name__)` call sites. A stdlib bridge means zero changes to those call sites while still getting JSON output + contextvar injection automatically.
- The processor chain is the right place to add redaction (`redaction.py`), context injection, and renderer selection (JSON vs Colourised). Each is a small pure function — testable in isolation.
- `loguru` is nice but its separation from the stdlib creates a second "kind" of logger; we'd still need a bridge for dependencies that log via stdlib. More friction than benefit.
- OTEL Logs would force us to pick an exporter today; deferring that keeps the SDK out of the critical path.

---

## R3. Metrics library

**Question**: What emits `/metrics`?

**Options**:

1. `prometheus-client` (the official Python client). Small, well-maintained, supports all the metric types we need. Text exposition only (OpenMetrics-compatible).
2. OpenTelemetry Metrics SDK + Prometheus exporter. Correct long-term; heavier now; fewer idiomatic examples in FastAPI.
3. `starlette-prometheus` / `prometheus-fastapi-instrumentator`. Convenience wrappers around (1). Opinionated — pre-baked metric set that doesn't exactly match our catalogue.

**Decision**: Option 1 — direct `prometheus-client` usage, wrapped behind our own `metrics.py` module.

**Rationale**:

- The pre-built wrappers define their own metric names and labels; migrating away from them later is its own refactor.
- `prometheus-client` registers at import time and is trivial to test-isolate via `CollectorRegistry` reset.
- OTEL Metrics is the long-term target but pulling the SDK now doubles the dependency surface for a feature we can swap in with a one-file change.

**Exposition endpoint**: `GET /metrics` mounted outside the auth gate. `Content-Type: text/plain; version=0.0.4; charset=utf-8` for OpenMetrics compatibility.

---

## R4. Trace propagation mechanism in-process

**Question**: How do we keep `trace_id` available inside every log line and inside every worker thread spawned by the compliance graph / LangGraph?

**Options**:

1. `threading.local()` — does not survive `asyncio.to_thread()` or `ThreadPoolExecutor` because each task runs on a fresh logical context.
2. `contextvars.ContextVar` + `contextvars.copy_context()` wrapper around executor submissions.
3. Thread through explicitly: every function takes a `ctx` argument.

**Decision**: Option 2.

**Rationale**:

- `contextvars` is the Python-native solution for async-friendly per-request state and is exactly what `asgi` already uses under the hood.
- `copy_context()` lets us ship the caller's context into worker threads without plumbing an explicit arg. We wrap `ThreadPoolExecutor.submit` once in `observability/tracing.py:_run_with_context` and call sites don't change.
- Explicit threading is a maintenance tax (every function signature grows) and still doesn't solve the deep-LangGraph node case. We'd end up needing `contextvars` anyway.

**Gotcha captured as a test**: `backend/tests/observability/test_context_survives_executor.py` verifies that a log line emitted from a `concurrent.futures.ThreadPoolExecutor` worker carries the caller's `trace_id`.

---

## R5. Scope of metric labels

**Question**: Which labels are allowed on which metrics, and which candidate labels are banned because of unbounded cardinality?

**Options considered for each candidate label**:

- `route` (path template, e.g. `POST /api/compliance/{doc_id}/run`) — **allowed**. Cardinality ≈ routes × methods ≈ 100.
- `method` (HTTP verb) — **allowed**. Cardinality ≤ 8.
- `status_class` (`1xx` / `2xx` / `3xx` / `4xx` / `5xx`) — **allowed**. Cardinality = 5.
- `agent` (5 known agents) — **allowed**. Cardinality = 5.
- `stage` (5 BMR stages + 5 compliance stage ids) — **allowed**. Cardinality ≤ 10.
- `status` on findings / runs (`compliant` / `non_compliant` / `uncertain` / `not_applicable` / `error` / `ok` / `failed` / `cancelled` / `completed` / `awaiting_legibility_review` / `pending` / `applied` / `pass` / `open` / `indeterminate` / `unevaluated`) — **allowed**. Cardinality ≤ 20 per metric; each metric binds a specific subset.
- `severity` (critical / major / minor / observation) — **allowed**. Cardinality = 4.
- `hitl_status` (auto_approved / system_confirmed / needs_review / user_approved / user_rejected / user_modified / unknown) — **allowed**. Cardinality = 7.
- `model` (model id, e.g. `gpt-4o-2024-08-06`, `claude-sonnet-4-6`) — **allowed but bounded**. Whitelist in settings; unknown models fold to `other`. Cardinality ≤ 10.
- `direction` (for LLM tokens: `prompt` / `completion`) — **allowed**. Cardinality = 2.
- `purpose` (LLM call purpose: `evaluator` / `orchestrator` / `summary` / `vision` / `discover_rules`) — **allowed**. Cardinality ≤ 10. Unknown folds to `other`.
- `kind` (exception class or failure reason; `errors_total`, `llm_call_failures_total`) — **allowed with fold**. Unknown folds to `Exception` / `other`. Cardinality ≤ 50 per metric.
- `mode` (dedup mode: `per_agent` / `cross_agent_preserve` / `cross_agent_collapse`) — **allowed**. Cardinality = 3.
- `gate_status` (HITL export gate: `READY` / `BLOCKED_BY_PENDING_FINDINGS` / `BLOCKED_BY_STALE_RESOLUTIONS`) — **allowed**. Cardinality ≤ 5.
- `action` (HITL resolution action: `CONFIRM` / `DISMISS` / `CORRECT`) — **allowed**. Cardinality = 3.
- `reason_type` (HITL dismiss reason: `OCR_MISREAD` / `ACCEPTABLE_VARIANCE` / `DUPLICATE_FINDING` / `OTHER` / `NONE`) — **allowed**. Cardinality = 5.
- `scope` (BMR rule scope: `same_page` / `cross_document` / `page_aggregate` / `checklist_synthesis`) — **allowed**. Cardinality = 4.
- `endpoint` (health route: `health` / `ready`) — **allowed**. Cardinality = 2.
- `run_id` / `doc_id` / `rule_id` / `finding_id` / `user_email` / `actor_id` — **BANNED**. Unbounded. Must appear in logs and traces only.

The authoritative whitelist is this list; `contracts/metrics.md` references it, `backend/app/observability/metrics.py` imports a frozen set `ALLOWED_LABELS` of these names, and `tests/observability/test_metrics_catalogue.py::test_no_banned_labels` fails if a registered metric uses any label not in the set.

**Decision**: Bind the whitelist in code. `backend/tests/observability/test_metrics_catalogue.py` asserts every metric registered in `metrics.py` uses only labels from the whitelist.

**Rationale**: Prometheus performance and cost are both cardinality-bound. One accidental `doc_id` label on `compliance_findings_total` produces ~100K series within a week of running the pilot; catching this at import time in a test is cheaper than noticing at scrape time in prod.

---

## R6. Finding-semantics — wire vs display

**Question**: Do we rename `auto_approved` → `system_confirmed` on the wire, in the persisted JSON, or only in the display?

**Options**:

1. Rename everywhere (wire, persisted data, UI). Clean. Breaks any external consumer of the persisted report format; forces a migration of every on-disk `compliance_result.json`.
2. Keep wire value; rename display only. Zero breaking change. Requires a one-line mapping in frontend + a tooltip that explains the history.
3. Keep everything as-is and fix only the palette. Minimum change. Doesn't address the word "approved" which is the confusing part.

**Decision**: Option 2 — display-only rename, with a scheduled follow-up spec for the wire migration.

**Rationale**:

- The on-disk reports at `backend/data/documents/*/compliance_result.json` are produced by runs the team has already investigated; rewriting them in place is a minor regression risk we don't need today.
- The source of the confusion is the label + colour, not the wire key. Fixing the display solves the user-visible bug immediately.
- A future migration can rewrite persisted reports forward when the team has bandwidth; the read path must handle both keys in the meantime (it already does).

**Display mapping (frontend)**:

```
wire               display label              palette                         tooltip
---------------    ----------------------     ----------------------------    --------------------------------
auto_approved      "System-confirmed"         neutral (slate/500)             "Model-only review — high confidence, no reviewer needed"
needs_review       "Needs review"             warning (amber/600)             "Awaiting reviewer confirmation"
user_approved      "Reviewer-approved"        success (emerald/600)           "Reviewer confirmed as valid"
user_rejected      "Reviewer-rejected"        destructive (red/600)           "Reviewer rejected as spurious"
user_modified      "Reviewer-modified"        info (blue/600)                 "Reviewer edited severity/description"
unknown            "Unknown"                  muted (muted-foreground)        "Missing HITL state — data integrity issue"
```

The `auto_approved` / `system_confirmed` palette choice is explicitly neutral, never success-green. Severity keeps owning the "is this bad?" colour.

---

## R7. `_deduplicate_findings` — fix vs rewrite

**Question**: Two agents producing the same `rule_id` — do we dedupe them together (current behaviour) or keep both (so attribution is per-agent)?

**Options**:

1. Keep current behaviour, but after global dedup, resync every `AgentReport.total_findings` to `len([f for f in global_findings if f.agent == ar.agent])` so counts match. Preserves the "one finding per rule in the final report" invariant.
2. Change the key from `rule_id` to `(agent, rule_id)` so each agent keeps its finding. Doubles the number of findings when agents overlap.
3. Allow the caller to pick via a `mode` parameter, default to attribution-preserving.

**Decision**: Option 3 with default `preserve_attribution` (= Option 2 for cross-agent calls). Keep the legacy `rule_id`-only behaviour available as `mode="collapse_across_agents"` for backward compatibility and for the per-agent call-site inside `assemble_agent_report`.

**Rationale**:

- In `assemble_agent_report` the dedup is per-agent (same rule evaluated on multiple pages inside one agent) — the `rule_id` key is correct there.
- Across agents, collapsing loses attribution. Both agents *did* evaluate the rule; both *did* produce findings; the only honest representation is to keep both. If a downstream step needs a single-row summary, it can group by rule after the fact.
- Adding a `mode` parameter makes the choice explicit at each call site, which is exactly what the constitution's "rule-as-data" spirit wants.

**Signature**:

```python
def _deduplicate_findings(
    findings: list[ComplianceFinding],
    *,
    mode: Literal["per_agent", "cross_agent_preserve", "cross_agent_collapse"] = "per_agent",
) -> list[ComplianceFinding]: ...
```

The global call site in `compliance_graph.py:424` passes `mode="cross_agent_preserve"`. A `compliance_dedup_merges_total{mode}` counter records collapses.

---

## R8. Health endpoints — split vs combined

**Question**: `/health` only, or `/health` (liveness) + `/health/ready` (readiness)?

**Decision**: Split. `/health` is liveness (process is up), `/health/ready` is readiness (can serve traffic — storage writable, rule bank loadable, event bus initialised). Kubernetes-style convention.

**Rationale**: The distinction matters when the pilot runs behind an orchestrator (k8s, Nomad, ECS) that needs to route traffic only to ready pods while still letting the liveness probe restart an unresponsive one. Cheap to ship both now.

---

## R9. Redaction — opt-in vs opt-out

**Question**: Is the redaction filter on by default, and what does it redact?

**Decision**: On by default. Rejects any log payload value that is (a) larger than 2 KiB, or (b) matches a permissive base64/PDF-bytes heuristic. Replaces with `"<redacted: oversized>"` and increments `errors_total{kind="LogRedaction"}` so we can detect the most common caller misuse.

**Rationale**:

- The catastrophic failure mode in log pipelines is shipping raw document text or LLM prompts into a log aggregator. Opt-out means one forgotten call dumps 50 MB.
- The detection heuristic is deliberately permissive — redacting false positives is safe; letting PDFs through is not.

---

## R10. When do we migrate to the OpenTelemetry SDK?

**Question**: Day 1, Day 90, Day 365?

**Decision**: Not part of Spec 006. Ship `opentelemetry-api` (the ids + interfaces) so the shape is compatible. The SDK + exporter migration is a follow-up spec driven by "we have somewhere to send the traces to."

**Rationale**: Adding the SDK without a destination is pointless and expensive (another dependency tree, another shutdown lifecycle to manage). The API alone gets us correct `traceparent` emission today and no rework when we add the SDK.

---

## Summary of dependencies

| Purpose | Dependency | Why this, not that |
|---|---|---|
| Structured logs | `structlog>=24` | Typed event dicts, processor chain, stdlib bridge |
| Metrics | `prometheus-client>=0.20` | Minimal, idiomatic, OpenMetrics-compatible |
| Trace ids + span shape | `opentelemetry-api>=1.25` | W3C ids without the SDK; swap-to-SDK is additive |

No new frontend dependencies. Styling changes use the existing `cn`, `tailwindcss`, and shadcn Badge.
