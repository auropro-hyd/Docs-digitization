# Data Model — Observability & Finding-Semantics

This document defines the in-memory entities introduced by Spec 006. None of them are persisted; observability state is transport-only (headers, log lines, metrics exposition). The finding-semantics additions extend the existing persisted shape additively — no field is removed.

---

## 1. TraceContext

**Purpose**: Identifies a single in-flight unit of work across process/thread/async boundaries. Crosses the wire as `traceparent`.

**Fields**:

| Field | Type | Format | Notes |
|---|---|---|---|
| `version` | `str` | 2 hex chars | Always `"00"` for v0 (W3C v3 spec). |
| `trace_id` | `str` | 32 hex chars | 128-bit id. Never all-zero (a valid trace_id has at least one non-zero byte). |
| `span_id` | `str` | 16 hex chars | 64-bit id. Never all-zero. For incoming requests this is the *parent* span id; the handler itself runs under a new span. |
| `parent_span_id` | `str \| None` | 16 hex chars | Present only on child spans. |
| `flags` | `str` | 2 hex chars | W3C trace flags. `01` = sampled. Default `01`. |
| `tracestate` | `str` | opaque | Echoed unchanged on response; not parsed. Empty string if absent. |

**Invariants**:

- `trace_id != "0" * 32`; `span_id != "0" * 16` — enforced by the parser. Invalid ids → the parser rejects and the middleware mints a new context.
- `TraceContext` is frozen (`@dataclass(frozen=True)`). A child span is constructed via `ctx.child_span()`, not by mutation.

**Lifecycle**:

1. Middleware receives the request → parses `traceparent` → if absent/malformed, mints a fresh id.
2. Middleware sets `TRACE_CTX.set(ctx)` in a `ContextVar`.
3. Handler runs under that context; any `@traced(name)` decorator creates a child span via `TRACE_CTX.get().child_span(name)` and re-binds the var for the span's duration.
4. On response, middleware emits `traceparent` and `X-Request-Id` (= `trace_id`) and clears the var via a context-manager `finally`.

**Cross-boundary propagation**:

- Async (`await`): inherits automatically via `contextvars` semantics.
- `asyncio.to_thread(fn, *args)`: inherits via Python's built-in `copy_context()`.
- `concurrent.futures.ThreadPoolExecutor.submit(fn, ...)`: does NOT inherit by default. We provide `observability.tracing.submit_with_context(executor, fn, ...)` which wraps with `copy_context().run`.
- LangGraph nodes: nodes run inside `asyncio.to_thread` in our usage, so they inherit. Verified by `test_context_survives_executor.py`.

---

## 2. RequestScope

**Purpose**: Per-request bundle of *business* context that augments `TraceContext` with domain ids. Enriches every structured log line without the caller having to re-pass them.

**Fields** (all optional, `None` when unknown):

| Field | Type | Set when | Source |
|---|---|---|---|
| `actor_id` | `str \| None` | `X-Actor-Id` header validated | `require_actor` dependency |
| `doc_id` | `str \| None` | route pattern contains `{doc_id}` | middleware path params |
| `run_id` | `str \| None` | route pattern contains `{run_id}` OR `bind_context` call in service | route + BMR service |
| `stage` | `str \| None` | Inside a stage function | `@traced("bmr.stage.ingest")` or explicit `bind_context(stage=…)` |
| `agent` | `str \| None` | Inside a compliance agent | `orchestrator.py` at agent entry |
| `rule_id` | `str \| None` | Inside a rule evaluation | `rule_eval.py` per-rule loop |

**Invariants**:

- `RequestScope` is a `dict`-backed immutable snapshot. Calls to `bind_context(key=value)` return a new snapshot and set the ContextVar; they never mutate the previous snapshot.
- Keys are drawn from a closed set (above). An unknown key raises `ValueError` at bind time — prevents ad-hoc field proliferation.

**Emission into logs**:

A structlog processor (`_inject_context_processor` in `logging.py`) reads `REQUEST_SCOPE.get()` and merges its non-None fields into every log `event_dict`. Callers need no awareness.

---

## 3. LogEvent

**Purpose**: Every structured log record emitted by the domain has an `event` field drawn from a closed catalogue (see `contracts/events.md`). Ad-hoc event names are forbidden.

**Shape** (JSON mode):

```json
{
  "ts": "2026-04-22T10:15:32.104Z",
  "level": "INFO",
  "logger": "app.bmr.workflow.stages",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "parent_span_id": null,
  "actor_id": "qa.reviewer",
  "doc_id": "f9f7e1b6-d7a3-415c-8275-795ec0c69888",
  "run_id": null,
  "stage": "compliance",
  "agent": "alcoa",
  "event": "compliance.agent.completed",
  "duration_ms": 12431,
  "findings_count": 14,
  "msg": "agent completed"
}
```

**Shape** (dev / colourised mode):

```
10:15:32.104 INFO  compliance.agent.completed alcoa findings=14 dur=12.43s trace=4bf92f35…
    logger=app.bmr.workflow.stages doc=f9f7e1b6-d7a3-… actor=qa.reviewer
```

**Invariants**:

- `event` is mandatory. A log call missing `event=` is caught by a linter/test that scans for `logger.info("`/`logger.warn("` / etc. calls and flags missing `event=`.
- `msg` is the human-readable tail; all structured data lives in the named fields.
- Field names are snake_case; nested structures are flattened (`error.kind`, `error.stack`) rather than nested JSON to keep log shippers happy.

---

## 4. Metric

**Purpose**: Single source of truth for every Prometheus metric emitted by the application. Registered exactly once at module import; consumed by name from domain code.

**Kinds**:

- **Counter** — monotonic, never decreases. E.g. `http_requests_total`.
- **Histogram** — bucketed distribution. E.g. `http_request_duration_seconds`. Default buckets tuned per metric (see `contracts/metrics.md`).
- **Gauge** — instantaneous value, up and down. E.g. `bmr_runs_in_flight`.

**Registration**:

```python
# app/observability/metrics.py
from prometheus_client import CollectorRegistry, Counter, Histogram, Gauge

REGISTRY = CollectorRegistry()

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests by route + status class.",
    labelnames=("method", "route", "status_class"),
    registry=REGISTRY,
)
# ... etc
```

Domain code imports by name:

```python
from app.observability.metrics import HTTP_REQUESTS
HTTP_REQUESTS.labels(method="POST", route="/api/...", status_class="2xx").inc()
```

**Invariants**:

- Label names are from the cardinality whitelist (R5 in `research.md`). A test iterates the registry and asserts every metric's labels are whitelisted.
- `Metric.describe()` returns its name + labels + description; `contracts/metrics.md` lists every registered metric and is kept in sync via a test (`test_metrics_catalogue.py`).

**Test isolation**:

A pytest fixture resets the registry between tests (via `REGISTRY._names_to_collectors.clear()` or by rebuilding the registry). Metric increments in test A cannot leak into test B.

---

## 5. HITLDisplayState

**Purpose**: Bridge between the persisted `hitl_status` wire value and what the UI renders. Frontend-owned; documented here so backend and frontend agree.

**Type (TypeScript)**:

```ts
type HitlWireValue =
  | "auto_approved"       // legacy; display as "System-confirmed"
  | "system_confirmed"    // reserved for future wire migration — treated identically to auto_approved
  | "needs_review"
  | "user_approved"
  | "user_rejected"
  | "user_modified"
  | "unknown";             // NEW — explicit missing-data state

type HitlDisplayState = {
  wire: HitlWireValue;
  label: string;              // human-readable
  palette: "success" | "warning" | "destructive" | "neutral" | "info";
  tooltip: string;            // why this badge is here
  icon: LucideIcon;
};
```

**Mapping** (definitive):

| Wire | Label | Palette | Icon |
|---|---|---|---|
| `auto_approved` | "System-confirmed" | `neutral` | `ShieldCheck` |
| `system_confirmed` | "System-confirmed" | `neutral` | `ShieldCheck` |
| `needs_review` | "Needs review" | `warning` | `Eye` |
| `user_approved` | "Reviewer-approved" | `success` | `ThumbsUp` |
| `user_rejected` | "Reviewer-rejected" | `destructive` | `ThumbsDown` |
| `user_modified` | "Reviewer-modified" | `info` | `Pencil` |
| `unknown` | "Unknown" | `neutral` | `CircleHelp` |

**Invariants** (enforced by unit test):

- `neutral` is not `success`. The chosen hue for `neutral` must not equal the hue for `success` in the Tailwind theme; the test reads `getComputedStyle` on rendered test badges and asserts inequality on the background colour.
- No wire value defaults to another value; `HITL_CONFIG[wire] ?? HITL_CONFIG.unknown` is the only allowed fallback (never `auto_approved`).

---

## 6. FindingRecord (no schema change, FR-014 only)

The persisted `ComplianceFinding` shape is unchanged. Spec 006 adds:

- A *new optional* field `dedup_mode` at the report root (`"per_agent" | "cross_agent_preserve" | "cross_agent_collapse"`). Defaults to `"cross_agent_preserve"` on new reports; absent on legacy reports, which are read in `"cross_agent_collapse"` mode for compatibility.
- A *new optional* field `dedup_winner_agent` on individual findings — populated only when a finding is the survivor of a cross-agent collapse. Empty on the default path.

No existing field is removed or repurposed. The `_recompute_review_adjusted_scores` function reads old reports unchanged.

---

## 7. Context shutdown and cleanup

When a request ends (success, error, or client disconnect):

1. Middleware's `finally` clause resets both `TRACE_CTX` and `REQUEST_SCOPE` via `ContextVar.reset(token)` using the tokens saved at `set()`.
2. Any worker thread still running that holds a copied context does NOT leak — the copy is that thread's local view; when the thread finishes, it is GC'd.
3. WebSocket handlers hold the context for the life of the connection; on disconnect the same reset happens.

**Invariant**: After a request completes, no ContextVar carries state from that request. A unit test (`test_no_context_leak_between_requests`) hits two sequential requests and asserts the second does not see the first's `trace_id`.
