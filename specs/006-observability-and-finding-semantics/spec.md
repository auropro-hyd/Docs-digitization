# Feature Specification: Observability & Finding-Semantics Hardening

**Feature Branch**: `006-observability-and-finding-semantics`
**Created**: 2026-04-22
**Status**: Draft
**Input**: User description: "Set up observability (common correlation header, structured logs, metrics, traces, SoC/modularity, best practices) and fold in the compliance-report semantic fixes identified during PR #2 review — `auto_approved` colour confusion, inverted default, and cross-agent dedup attribution loss."

---

## Background (non-normative)

Spec 006 exists because two orthogonal classes of problem surfaced together during the PR #2 review and local walkthrough of the compliance viewer on `http://localhost:3100/compliance?doc=<id>`:

1. **Observability gap.** The platform is now a multi-stage, multi-agent pipeline (Compliance + BMR Spec 001–005). When something looks wrong in the UI — e.g. a category scores 0 while its findings are styled as if "approved" — there is no reliable way to pull the end-to-end story of one request out of the logs. There is no correlation header, no structured logging, no metrics surface, and no shared way to trace a single document's journey through OCR → extraction → per-agent evaluation → HITL → export. Debugging today requires grepping uncorrelated `print`/`logger.info` lines across three process domains.

2. **Finding-semantics gap.** The compliance UI conflates two orthogonal dimensions — the rule outcome (`compliant` / `non_compliant` / `uncertain`) and the reviewer-workflow state (`auto_approved` / `needs_review` / `user_*`). Findings that are *confirmed non-compliance with high model confidence* are painted green with a shield-checkmark and labelled "Auto-approved / Quality Confirmed", indistinguishable from "this check passed." The persisted report also carries two latent bugs — a `hitl_status` fallback of `"auto_approved"` on both server and client, and a cross-agent `_deduplicate_findings` that silently collapses attribution — that will bite the moment rule IDs overlap across agents.

Both classes need the same two things to be fixed sustainably: a structured, correlated view of the data flowing through the system, and explicit names for the things the UI is showing. Bundling them avoids two rounds of the same cross-cutting code touches.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Engineer traces one compliance run end-to-end by one identifier (Priority: P1)

An engineer (internal, on-call for pipeline issues) sees a compliance report that looks wrong. They open the backend log stream, paste the `X-Request-Id` from the UI's network panel (or the `traceparent` sent from the frontend), and get back every log line emitted while serving that run — API handler, per-agent evaluator calls, LLM requests, storage reads, HITL writes — in chronological order. Every line carries the same `trace_id`. The engineer can then filter by `stage=compliance` or `agent=alcoa` without re-deriving request boundaries.

**Why this priority**: Without a single correlating identifier, every production-grade investigation starts from "which run was this?" and burns 20–60 minutes reconstructing request boundaries by timestamp. This is the single highest-leverage observability feature — it costs nothing at idle and pays back on every triage.

**Independent Test**: Make a single authenticated request to `POST /api/compliance/{doc_id}/run` with `traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01`. Tail the backend log. Every log line produced while that request is in flight — including log lines emitted from worker threads spawned by the compliance graph — must carry `trace_id=4bf92f3577b34da6a3ce929d0e0e4736`. No lines from unrelated concurrent requests may carry the same `trace_id`.

**Acceptance Scenarios**:

1. **Given** the client sends a `traceparent` header, **When** the server handles the request, **Then** every log line for that request carries the same `trace_id` extracted from `traceparent`, and the response carries an `X-Request-Id` and `traceparent` echo.
2. **Given** the client does *not* send `traceparent`, **When** the server handles the request, **Then** the server mints a new W3C-valid `trace_id` + root `span_id`, echoes both back on the response, and logs under that id.
3. **Given** the compliance graph spawns worker threads for per-agent evaluation, **When** those workers emit log lines, **Then** the lines still carry the originating request's `trace_id` and `span_id`.
4. **Given** the client passes a malformed `traceparent`, **When** the server handles the request, **Then** the server logs a single `warn` (`trace.malformed_header`) and falls back to minting a new id — it never rejects the request.

---

### User Story 2 — Ops sees how healthy the pipeline is without reading logs (Priority: P1)

A platform operator scrapes `GET /metrics` with Prometheus (or any OpenMetrics-compatible agent) and gets counters + histograms for every public surface: HTTP request rate and latency by route, compliance run duration by stage and agent, finding volume by `(agent, status, severity, hitl_status)`, LLM calls and token usage by model, HITL resolution counts by action, BMR run status counts, and error counts by exception class. From those they build dashboards and alerts without touching application code.

**Why this priority**: Metrics are the only way to know whether the pipeline is working *before* someone opens a ticket. Counters + durations + error counts at the right cardinality catch regressions (a deployed change that doubled OCR latency, an agent that silently started returning no findings) within minutes.

**Independent Test**: Trigger three compliance runs across two documents with one deliberate failure injected. Scrape `/metrics`. The output must contain:

- `http_requests_total{route="POST /api/compliance/{doc_id}/run",status="2xx"} ≥ 2`
- `http_requests_total{route="POST /api/compliance/{doc_id}/run",status="5xx"} ≥ 1`
- `compliance_findings_total` broken down by `(agent, status, severity, hitl_status)` that sums to the number of findings produced
- `compliance_run_duration_seconds_count{stage="compliance",agent="alcoa"} ≥ 2`

**Acceptance Scenarios**:

1. **Given** any HTTP request, **When** it returns, **Then** `http_requests_total` and `http_request_duration_seconds` advance for the request's normalized route (path template, not the concrete path) and response status class.
2. **Given** a compliance run finishes, **When** metrics are scraped, **Then** `compliance_findings_total` has incremented for each emitted finding under the finding's `(agent, status, severity, hitl_status)` tuple.
3. **Given** an LLM call is made inside any agent, **When** it returns, **Then** `compliance_llm_calls_total{model}` advances and `compliance_llm_tokens_total{model,direction}` captures prompt and completion tokens.
4. **Given** an unhandled exception is raised in any route, **When** the response is returned, **Then** `errors_total{route, kind}` advances with `kind` = the exception class name.

---

### User Story 3 — Reviewer can tell "rule passed" from "finding auto-approved" at a glance (Priority: P1)

A QA reviewer opens the compliance viewer on a finished run. They see a category scoring 0/100 (every applicable rule failed). In the findings list beneath it, they see several entries. Each entry's **rule outcome** is visibly distinct from its **review state**: a critical/major non-compliance with "System-confirmed" review state is never styled the same way as a genuine compliant rule, and a "System-confirmed" badge is visibly different from a reviewer's "Approved" badge. The reviewer never confuses "the system is confident this is a failure" for "this check passed."

**Why this priority**: The current styling causes compliance reviewers to literally misread failures as passes. This is not a cosmetic issue — it is a regulatory audit-quality issue, and it is observable in the very document the user pulled up during this engagement.

**Independent Test**: Load the existing persisted report at `backend/data/documents/f9f7e1b6-d7a3-415c-8275-795ec0c69888/compliance_result.json`. In the rendered UI:

- The category card for `alcoa/legible` (score = 0) must not carry any success-green visual cue in its finding rows.
- Every finding with `hitl_status=auto_approved` must be styled with a colour that a reviewer would not confuse with a compliance pass (neutral / amber / yellow acceptable — *not* the same hue used for `user_approved`).
- Hover/tooltip text must explain "auto-approved ≠ compliant": e.g. "System-confirmed finding — high model confidence; no reviewer action required."

**Acceptance Scenarios**:

1. **Given** a finding has `status=non_compliant` and `hitl_status=auto_approved`, **When** it is rendered, **Then** its severity styling dominates the visual (critical findings stay red, major stays orange, etc.) and the HITL badge is a neutral chip that cannot be confused with a "compliant" indicator.
2. **Given** a finding is missing `hitl_status` entirely, **When** it is rendered server-side and client-side, **Then** the server does *not* impute `auto_approved` to it and the client does *not* default the badge to `auto_approved`. Both surface an explicit `unknown` state.
3. **Given** the legacy copy "Auto-approved / Quality Confirmed" appears in any UI / label / tooltip, **When** the feature ships, **Then** it is renamed to a phrase that does not imply compliance (e.g. "System-confirmed — high confidence", "Model-only review").

---

### User Story 4 — Operator can tell whether cross-agent rule overlap is hiding findings (Priority: P2)

When two agents evaluate the same `rule_id` and the global `_deduplicate_findings` collapses the result to one finding, today the losing agent's `total_findings` stays at its pre-dedup count — an invisible divergence between the per-agent tab badge and the per-agent filter on the global findings list. The operator needs two things: (a) deterministic invariant that tab count equals filtered-list count, and (b) a metric that surfaces when dedup is actually collapsing cross-agent entries.

**Why this priority**: Today's dataset does not share `rule_id` across agents, so the bug is dormant. It will surface when the rule bank grows and ALCOA + GMP (or Checklist + Reconciliation) evaluate overlapping rules. P2 because latent, not live.

**Independent Test**: Synthesise a report where two agents emit a finding for the same `rule_id`. After processing, `sum(ar.total_findings for ar in agent_reports) == len([f for f in report.findings if f.agent is not None])` and a metric `compliance_dedup_merges_total{mode="cross_agent"}` has advanced.

**Acceptance Scenarios**:

1. **Given** two agents produce a finding for the same `rule_id`, **When** the report is assembled, **Then** each agent's `total_findings` equals the number of findings in the global list carrying that agent's id — no silent divergence.
2. **Given** the global dedup collapses a cross-agent duplicate, **When** the collapse happens, **Then** a structured log line `compliance.finding.deduped` carries both agents' ids and the winning agent, and the `compliance_dedup_merges_total` counter advances.
3. **Given** the feature is off, **When** the old dedup path is used, **Then** tests that asserted the old behaviour keep passing (backward-compatible default).

---

### User Story 5 — Logs carry enough business context to skip opening a debugger (Priority: P2)

When an engineer reads a single log line in isolation, it answers: who (`actor_id`), what (`event`, `route`), for which document (`doc_id` or `run_id`), and at which stage (`stage`, `agent`). Free-text messages are not removed, but the context fields carry most of the diagnostic weight, and the engineer can pivot from one line to "everything on this doc in the last hour" without grep gymnastics.

**Why this priority**: Structured context is what turns `grep | head` into "Loki/Datadog filter," and it is the difference between 10-minute and 1-hour triage on a production issue.

**Independent Test**: Scan any representative log window. Every line in JSON mode contains at least `ts`, `level`, `trace_id`, `span_id`, `logger`, `event`, `msg`. Where the request is document-scoped, it also contains `doc_id`. Where it is run-scoped, `run_id`. Where it is authenticated, `actor_id`.

**Acceptance Scenarios**:

1. **Given** the service runs in JSON log mode, **When** any line is emitted, **Then** it parses as JSON and has the mandatory fields above.
2. **Given** a log call inside a compliance stage, **When** it runs, **Then** the line carries `stage=<stage_id>` and (if applicable) `agent=<agent_id>` without the caller having to pass them explicitly (set via context binding at stage entry).
3. **Given** the service runs in dev (non-JSON) mode, **When** a line is emitted, **Then** the same fields are present, pretty-printed for terminal readability, colour-keyed by level.

---

### User Story 6 — Latent `hitl_status` default is removed end-to-end (Priority: P2)

A finding that reaches the penalty calculator ([backend/app/api/routes/compliance.py:94](backend/app/api/routes/compliance.py#L94)) or the client badge renderer ([frontend/src/components/compliance/findings-table.tsx:110](frontend/src/components/compliance/findings-table.tsx#L110)) without an explicit `hitl_status` is treated as `unknown` — not silently mapped to `auto_approved`. The risk of a contract drift between the evaluator and the report downstream producing "approved" findings out of thin air is eliminated.

**Why this priority**: Defence-in-depth for User Story 3. Today the evaluator always sets `hitl_status`, so this is invisible; the moment a refactor omits the field, findings will accrue penalty + green "approved" badges silently. Fixing the defaults closes the hole before it opens.

**Independent Test**: Hand-construct a persisted report where exactly one finding has no `hitl_status` key. Load the report via `GET /compliance/{doc_id}/report`. The returned finding must have `hitl_status: "unknown"` (explicit), the score decomposition must either exclude it or flag it as `hitl_status=unknown` (never treat as `auto_approved`), and the client must render it with a neutral/warning badge explicitly saying "unknown."

**Acceptance Scenarios**:

1. **Given** a persisted finding has no `hitl_status` field, **When** `_score_from_findings` runs, **Then** it is either excluded from scoring or assigned an explicit `"unknown"` that the caller can choose to include/exclude — the default must not be `"auto_approved"`.
2. **Given** a finding arrives in the UI with `hitl_status: undefined`, **When** the badge renders, **Then** it shows an explicit "unknown" neutral badge with a tooltip, not the success-coloured auto-approved badge.

---

### Edge Cases

- **Trace id clash across requests**: minted trace ids are 128-bit random; collision probability is negligible but if collision is observed in tests, the system must not cross-wire context between requests.
- **Long-running background work**: BMR runs orchestrated via `asyncio.to_thread` and LangGraph must propagate the trace/span context into worker threads (see `context.py` in the data model). A worker that loses context must self-log `trace.context.lost` and continue rather than emit uncorrelated lines.
- **Very high cardinality on finding labels**: metrics labelled by `(agent, status, severity, hitl_status)` stay bounded (≤ 5×4×4×5 = 400 combinations). Labels like `rule_id` are deliberately NOT metric labels (unbounded) — they belong in structured logs and traces only.
- **High-frequency log volume**: the default non-JSON (dev) logger must not drop lines; the JSON production logger must be line-buffered and stdout-backed so container log drivers can rotate it.
- **Sensitive data in logs**: log payloads must never include raw document text, OCR chunks, LLM prompts, or user PII by default. Only stable ids + counts + status fields.
- **Metrics endpoint load**: `/metrics` must be safe to scrape at 10 s intervals from at least two concurrent Prometheus instances without blocking application traffic; it is served outside the auth gate.
- **Legacy compliance reports**: persisted reports authored before this feature may lack some of the new fields (`hitl_status=unknown` semantics, supersession metadata). The read-path normalises old reports forward; it never rewrites them on disk unless the reviewer triggers a save.

---

## Requirements *(mandatory)*

### Functional

- **FR-001 (Correlation header)**: Every HTTP response must carry `traceparent` (W3C Trace Context v3, `version-trace_id-parent_id-flags`) and a human-readable `X-Request-Id` echoing the same trace_id in hex. If the request carried `traceparent`, both values derive from it; if not, both are minted server-side.
- **FR-002 (Log correlation)**: Every log line emitted on behalf of a request carries that request's `trace_id` and the currently active `span_id`. This must hold across `run_in_executor` / `asyncio.to_thread` / `ThreadPoolExecutor` — i.e. the context must survive thread handoffs.
- **FR-003 (Structured log schema)**: Log records are JSON in production and colourised plain-text in dev. Mandatory fields: `ts`, `level`, `logger`, `trace_id`, `span_id`, `event`, `msg`. Optional contextual fields: `actor_id`, `doc_id`, `run_id`, `stage`, `agent`, `rule_id`, `duration_ms`, `error.kind`, `error.stack`.
- **FR-004 (Metrics surface)**: The service exposes `GET /metrics` in OpenMetrics text format. It is unauthenticated, not subject to CORS, served by the same app, and returns in ≤ 200 ms at p95 under normal load.
- **FR-005 (Named metrics)**: The service defines a closed set of named metrics (see `contracts/metrics.md`). Ad-hoc metric creation at call sites is forbidden — every metric used is defined in `app/observability/metrics.py` and imported by name.
- **FR-006 (Business context binding)**: At the entry of every known cross-cutting scope (HTTP handler, compliance stage, BMR stage, HITL operation), the request-scoped context binds the relevant business ids (`doc_id`, `run_id`, `stage`, `agent`) so downstream log lines inherit them automatically.
- **FR-007 (Fail-open observability)**: A failure in the observability stack (metric registry exception, log handler crash, trace parse error) must never block, delay, or corrupt the business response. The observability layer logs its own failure (via a fallback path) and the handler continues.
- **FR-008 (No PII / no raw content in logs)**: Logs must not contain raw document content, LLM prompts, OCR text, or user PII. A redaction filter rejects any log payload whose value is larger than 2 KiB or looks like base64/PDF bytes.
- **FR-009 (Health endpoints)**: `GET /health` returns 200 if the process is alive; `GET /health/ready` returns 200 only if storage is writable, the pilot rule bank loads, and the default event bus is initialised.
- **FR-010 (Traceparent echo on WebSocket)**: The BMR `/ws` endpoint attaches `trace_id` from the connecting request to every event envelope emitted on that connection, and logs connection lifecycle under that trace.
- **FR-011 (Finding-semantics rename)**: The UI label `auto_approved` is renamed `system_confirmed` in all new copy; legacy persisted reports continue to carry `hitl_status=auto_approved` as data (no wire-format break), but the *display* resolves it to the new copy. A migration note states both the data key and the display string.
- **FR-012 (Non-success styling for model-confirmed findings)**: `system_confirmed` (aka `auto_approved`) badges are rendered in a neutral/warning palette — never the same hue as `user_approved`, `compliant`, or "OK" states. Severity (`critical`/`major`/`minor`/`observation`) drives the dominant finding colour; HITL state is a secondary chip.
- **FR-013 (Explicit unknown HITL state)**: Both the server-side penalty calculator and the client-side badge renderer treat missing `hitl_status` as an explicit `unknown` value with neutral styling. Defaults of `auto_approved` are removed from [backend/app/api/routes/compliance.py:94](backend/app/api/routes/compliance.py#L94) and [frontend/src/components/compliance/findings-table.tsx:110](frontend/src/components/compliance/findings-table.tsx#L110).
- **FR-014 (Cross-agent dedup attribution)**: `_deduplicate_findings`, when invoked globally across agents, either (a) dedupes by `(agent, rule_id)` so attribution is preserved — preferred default, or (b) dedupes by `rule_id` and then resyncs every `AgentReport.total_findings` to the count of globally surviving findings carrying that agent, so the tab badge and the filtered list cannot diverge. The chosen mode is recorded in the report under `report.dedup_mode`.
- **FR-015 (Dedup observability)**: Every cross-agent dedup collapse emits a `compliance.finding.deduped` structured log and increments `compliance_dedup_merges_total{mode="cross_agent"}`.
- **FR-016 (Existing auth gate unchanged)**: The `require_actor` gate introduced in PR #2 remains the sole auth boundary on BMR endpoints; `/metrics` and `/health*` are explicitly *outside* the auth gate because they are platform-internal surfaces.
- **FR-017 (No behaviour change in compliance scoring for existing reports)**: Relabelling `auto_approved` → `system_confirmed` in the UI must not change any numeric score; the review-adjusted scoring formula is untouched. Scoring changes are explicitly out of scope.

### Non-functional

- **NFR-001 (Overhead)**: The added middleware, logging, and metrics layers must not increase p95 request latency by more than 5 ms on a warm cache for an idle request (baseline measured before the change).
- **NFR-002 (Cardinality ceiling)**: No metric label may have unbounded cardinality. Candidate high-risk labels (`doc_id`, `run_id`, `rule_id`, `user_email`) are forbidden as metric labels and must appear only in logs/traces.
- **NFR-003 (Zero business code import from FastAPI)**: `app/bmr/` and `app/compliance/` modules may not import from `fastapi`, `starlette`, `prometheus_client`, or the observability package except through a thin, injected interface (`Logger`, `Tracer`, `MetricRegistry` protocols). This preserves the domain / transport separation already established in the codebase.
- **NFR-004 (Test isolation)**: The test suite must not observe real metric counters across tests; the metric registry is reset between tests so counter increments in test A cannot leak into test B.
- **NFR-005 (Documentation)**: Every new public symbol has a one-line docstring. The `contracts/` folder in this spec lists every metric name, label set, and type, and every log event name, so downstream dashboard and alert authors have a single source of truth.

### Key Entities

- **TraceContext**: The immutable tuple `(trace_id, span_id, parent_span_id, flags)` that identifies a single in-flight unit of work. Crosses process boundaries via the `traceparent` header. Lives in a `contextvars.ContextVar` so it inherits across `await` and across `asyncio.to_thread` without explicit plumbing.
- **RequestScope**: The per-request bundle of business context (`actor_id`, `doc_id`, `run_id`, `stage`, `agent`) that augments `TraceContext`. Bound at middleware entry and re-bound at stage entry. Emitted into every structured log line automatically.
- **LogEvent**: A named, documented structured event (`compliance.run.started`, `compliance.finding.emitted`, `bmr.stage.entered`, …). Every `logger.info/warn/error` call inside the domain passes `event=<name>` explicitly. Ad-hoc event names are forbidden — the catalogue lives in `contracts/events.md`.
- **Metric**: A named Prometheus counter, gauge, or histogram, registered exactly once at import time, documented in `contracts/metrics.md`.
- **HITLDisplayState**: The frontend's mapping from `hitl_status` to label + badge style. A new `unknown` state is added; `auto_approved` is relabelled `system_confirmed` in display but retained as the wire key until a scheduled migration.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Given any request to any BMR or compliance endpoint, the engineer can retrieve every log line produced while handling that request by grepping on a single `trace_id` — without additional joins. Time from "UI screenshot" to "full request timeline" drops from 10+ minutes to under 60 seconds on a representative log window.
- **SC-002**: `GET /metrics` returns all defined metrics in under 200 ms at p95, under a scrape load of 2 concurrent consumers at 10 s intervals.
- **SC-003**: Zero compliance findings with `status=non_compliant` are rendered with the same success-green hue as `status_indicator.auto_approved.className` was using prior to this feature. Verified by a unit test scanning the rendered DOM class list for the forbidden class on a fixture where the current UI fails.
- **SC-004**: No finding across the regression suite arrives at the penalty calculator without an explicit `hitl_status`; if one does, it is classified `unknown` and contributes a neutral, test-visible signal — not a silent `auto_approved`.
- **SC-005**: Given two agents emitting findings for the same `rule_id`, `sum(ar.total_findings) == len([f for f in report.findings if f.agent is not None])` holds on the server response and the UI-rendered tab badges match the UI-rendered filtered findings count.
- **SC-006**: p95 latency overhead introduced by the observability layer is ≤ 5 ms on a warm path; measured before and after on a single-thread benchmark shipped under `backend/tests/benchmark/`.
- **SC-007**: The engineer can replace the JSON log handler with an OpenTelemetry OTLP exporter by editing *one* config in `app/observability/logging.py` — no business-code change. Demonstrated by a one-commit "swap to OTLP" example in `quickstart.md` that results in logs appearing in Jaeger/Tempo within 5 minutes of config.

---

## Assumptions

- W3C Trace Context headers (`traceparent`, optional `tracestate`) are the correct wire format, consistent with OpenTelemetry and modern APM vendors (Datadog, Honeycomb, Tempo, Jaeger). No bespoke header format.
- The platform does not yet run an OpenTelemetry collector. Feature 006 ships the *API-compatible* shape (W3C ids, OTEL-style span naming, Prometheus metric naming) so swapping in an OTEL SDK and exporters later is additive, not a rewrite.
- Deployments are single-process (one uvicorn worker) for v0. Multi-worker tracing still works via headers; *in-process* correlation uses `contextvars`.
- No structured log aggregator is deployed yet; JSON-to-stdout is sufficient. Production grade aggregators (Loki, Datadog, Elastic) are all compatible with the chosen shape.
- Frontend changes required by User Story 3/6 ship in the same PR as backend changes, because the label/style change is only meaningful end-to-end.
- The existing compliance-report wire format is not versioned; this spec treats persisted reports as forward-compatible — new optional fields are added, no field is removed or repurposed.
- "Best practices" means: domain code does not import transport; public contracts are documented under `contracts/`; metric cardinality is bounded; defaults fail closed; redaction is on by default. These are encoded as FR-/NFR- items above, not left implicit.

---

## Out of Scope

- Distributed tracing backends (Jaeger, Tempo, Honeycomb) — we ship the shape; the exporter is a follow-up config.
- Log aggregation stack (Loki/ELK) — out of scope; JSON-to-stdout is the contract.
- Alert rules, dashboards, SLOs — consumer-owned; we publish the metric catalogue.
- Auth/SSO/RBAC integration — unrelated; `require_actor` from PR #2 remains the auth surface.
- Compliance scoring formula changes — explicitly preserved (FR-017).
- Migration of `hitl_status` wire values from `auto_approved` to `system_confirmed` — deferred (display-only rename in this feature; wire key migration is a later spec).
- Re-architecture of `_deduplicate_findings` beyond the fix that resolves the attribution divergence (FR-014).
