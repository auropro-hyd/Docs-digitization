# Tasks — Observability & Finding-Semantics

Execution breakdown for Feature 006. Five tracks, parallel-safe after Track 1 (Foundation) lands. Each task has a clear artefact and acceptance signal.

---

## Track 1 — Observability foundation

Must land first — everything else depends on `app/observability/*` being importable.

### T1.1 — Add dependencies

- Add to `backend/pyproject.toml`:
  - `structlog>=24`
  - `prometheus-client>=0.20`
  - `opentelemetry-api>=1.25`
- Lock via `uv lock`.
- Acceptance: `uv sync` succeeds; `uv run python -c 'import structlog, prometheus_client, opentelemetry'` runs cleanly.

### T1.2 — Package skeleton

- Create `backend/app/observability/` with `__init__.py`, `context.py`, `logging.py`, `metrics.py`, `middleware.py`, `tracing.py`, `redaction.py`, `protocols.py`, `health.py`.
- Each module exposes a narrow public surface via `__all__`.
- Acceptance: `import app.observability` succeeds; no domain code imported.

### T1.3 — `TraceContext` + `contextvars` (`context.py`)

- Frozen dataclass per `data-model.md`.
- `TRACE_CTX: ContextVar[TraceContext | None]`, `REQUEST_SCOPE: ContextVar[dict[str, Any]]`.
- Helpers: `current_trace()`, `current_scope()`, `bind_context(**fields) -> Token`, `reset(token)`.
- Closed set of allowed scope keys: `actor_id, doc_id, run_id, stage, agent, rule_id`.
- Acceptance: `backend/tests/observability/test_context.py` covers bind/reset, scope-key validation, and `contextvars` inheritance across `await`.

### T1.4 — Trace header parser + emitter (`tracing.py`)

- `parse_traceparent(value: str) -> TraceContext | None` per `contracts/trace-header-contract.md`.
- `mint_trace() -> TraceContext` using `secrets.token_hex`.
- `child_span(name: str) -> TraceContext`.
- `submit_with_context(executor, fn, *args, **kwargs)` — wraps with `copy_context()`.
- `@traced(name: str)` decorator — creates a child span, binds it, emits `span.started` / `span.ended` structured logs.
- Acceptance: `tests/observability/test_trace_propagation.py`, `test_context_survives_executor.py`, `test_traceparent_parse.py`.

### T1.5 — Structlog config + stdlib bridge (`logging.py`)

- `configure(log_level: str, log_mode: Literal["json", "dev"])`.
- Processors in order: `add_log_level`, `TimeStamper`, `_inject_context_processor`, `_inject_trace_processor`, `_redact_processor`, `JSONRenderer` or `ConsoleRenderer`.
- Stdlib bridge: `structlog.stdlib.ProcessorFormatter` on the root logger so existing `logging.getLogger(__name__)` calls emit through the same pipeline.
- `get_logger(name: str) -> structlog.BoundLogger`.
- Acceptance: `tests/observability/test_logging.py` asserts JSON mode output has mandatory fields; dev mode is parseable by a regex.

### T1.6 — Redaction filter (`redaction.py`)

- Reject values > 2 KiB; reject values matching `%PDF` prefix or long base64-looking strings.
- Emits `errors_total{kind="LogRedaction"}`.
- Acceptance: `tests/observability/test_redaction.py` fuzzes with PDF bytes, large strings, and a base64 blob.

### T1.7 — Prometheus metrics registry (`metrics.py`)

- All metrics from `contracts/metrics.md` registered exactly once on a module-level `REGISTRY`.
- `__all__` lists every metric name uppercase.
- `reset_for_tests()` helper.
- Acceptance: `tests/observability/test_metrics_catalogue.py` asserts registered set == expected set AND every label is on the whitelist.

### T1.8 — FastAPI middleware (`middleware.py`)

- On request: parse `traceparent` → bind `TraceContext` → bind `RequestScope` from path params (`doc_id`, `run_id`) → emit `trace.request.started`.
- On response: emit `traceparent` + `X-Request-Id`, append to `Access-Control-Expose-Headers`, observe `http_requests_total` + `http_request_duration_seconds` + `http_request_body_bytes`, emit `trace.request.finished`, reset ContextVars.
- Exception handler: emit `error.unhandled` + `errors_total{route, kind}`, re-raise.
- Acceptance: `tests/observability/test_middleware.py` covers inbound trace, outbound echo, minted trace, malformed header, exception path.

### T1.9 — Health + metrics endpoints (`health.py`)

- `GET /health` → `{"status":"ok"}`.
- `GET /health/ready` → checks storage dir writable, rule bank loadable, event bus initialised.
- `GET /metrics` → `generate_latest(REGISTRY)` with correct content-type.
- All three mounted outside the `require_actor` gate.
- Acceptance: `tests/observability/test_health_endpoints.py`.

### T1.10 — Wire into `main.py`

- Call `observability.logging.configure(...)` at import.
- Add middleware at the top of the stack.
- Include the health router.
- Remove the ad-hoc `logging.basicConfig(...)` block.
- Acceptance: existing BMR + compliance tests stay green; new `test_lifespan_trace.py` shows startup lines have a `trace_id`.

---

## Track 2 — Wire observability into domain code

Thin wiring — no logic changes. Mostly one-liners per module.

### T2.1 — BMR workflow `bind_context`

- In `app/bmr/workflow/service.py:start_run` → `bind_context(run_id=run_id, doc_id=spec.package_id)`.
- In `app/bmr/workflow/stages.py`, each stage function → `@traced("bmr.stage.<name>")` and `bind_context(stage=...)` at entry.
- Acceptance: a BMR run with a `traceparent` produces log lines tagged with `stage=ingest`, `stage=legibility_and_classification`, etc. Verified by grep in `tests/bmr/observability/test_bmr_trace.py`.

### T2.2 — BMR events carry `trace_id`

- `app/bmr/events/__init__.py:publish` — include `trace_id` from `current_trace()` in the envelope.
- Acceptance: WebSocket subscribers receive events with `trace_id` in payload; covered in `tests/bmr/events/test_bus.py`.

### T2.3 — HITL `bind_context`

- `app/bmr/hitl/service.py` — wrap `record_resolution`, `record_correction`, `export_report` with `bind_context(run_id=run_id)`.
- Acceptance: HITL logs tagged with run id without explicit kwargs.

### T2.4 — Compliance agent tracing

- `app/compliance/orchestrator.py` — `bind_context(doc_id=doc_id, agent=agent_id)` at the top of each agent execution.
- Wrap the per-agent `ThreadPoolExecutor.submit` in `submit_with_context`.
- Acceptance: `tests/compliance/test_agent_trace.py` asserts agent lines carry `agent=alcoa` etc.

### T2.5 — LLM call instrumentation

- Wherever LLM calls are made (`app/compliance/evaluator.py`, `context_builder.py`, `orchestrator.py`), time the call, record `llm_calls_total` + `llm_call_duration_seconds` + `llm_tokens_total`, emit `llm.call.*` events.
- Acceptance: `tests/observability/test_llm_metrics.py` runs a fake LLM and asserts all three metrics advance.

### T2.6 — Remove stray `print()` / `logger.info("…")` without `event=`

- Scan + rewrite in `app/bmr/`, `app/compliance/`, `app/api/routes/bmr_*.py`, `app/api/routes/compliance.py`.
- Acceptance: a lint-style test (`tests/observability/test_no_event_less_logs.py`) scans source and fails when it finds `logger.info("…")` without `event=`.

---

## Track 3 — Compliance finding-semantics (server)

### T3.1 — Remove `auto_approved` default in penalty calculator

- `backend/app/api/routes/compliance.py:_score_from_findings:94` — `status = str(f.get("hitl_status")) or "unknown"`, and treat `unknown` as excluded from penalty unless `include_unknown=True`.
- Acceptance: `tests/compliance/test_finding_semantics.py::test_missing_hitl_not_silently_approved`.

### T3.2 — `_deduplicate_findings` modes

- Refactor signature per `research.md` R7: `mode: Literal["per_agent", "cross_agent_preserve", "cross_agent_collapse"]`.
- Per-agent call in `evaluator.py:887` passes `mode="per_agent"` (existing behaviour).
- Global call in `compliance_graph.py:424` passes `mode="cross_agent_preserve"` (new default).
- Emit `compliance.finding.deduped` + `compliance_dedup_merges_total` when collapse occurs.
- Persisted report adds optional `dedup_mode` field.
- Acceptance: `tests/compliance/test_dedup_attribution.py` for all three modes.

### T3.3 — Attribution resync (fallback)

- If a report is configured in `cross_agent_collapse` mode (opt-in / legacy), after collapse, resync each `AgentReport.total_findings` to `sum(1 for f in report.findings if f.agent == ar.agent)`.
- Acceptance: unit test asserts `ar.total_findings == <global count filtered by agent>` for every agent in both modes.

### T3.4 — Metric wiring in `compliance_graph.py`

- On agent completion → `compliance_agent_duration_seconds.observe(...)`.
- On finding creation → `compliance_findings_total.labels(...).inc()`.
- On full run completion → `compliance_runs_total{status}` + `compliance_run_duration_seconds`.
- Acceptance: `tests/compliance/test_metrics_emitted.py`.

---

## Track 4 — Compliance finding-semantics (client)

### T4.1 — Extract `HITLBadge` component

- New file `frontend/src/components/compliance/hitl-badge.tsx` with `<HITLBadge status={...} />`.
- `findings-table.tsx` imports and uses it.
- Other consumers (`compliance-report.tsx`, any places rendering a HITL state) switch to the same component.
- Acceptance: grep shows one definition site; `npm run typecheck` passes.

### T4.2 — Remove `|| HITL_CONFIG.auto_approved` default

- `HITL_CONFIG[status] ?? HITL_CONFIG.unknown` as the only fallback.
- Acceptance: `frontend/tests/compliance/findings-table.test.tsx::renders unknown badge for missing status`.

### T4.3 — Rename display + repaint palette

- Update `HITL_CONFIG` per `contracts/hitl-display-contract.md`.
- Add `unknown` entry.
- Update `status-indicator.tsx:19` and `processing-labels.ts:20` to the new label.
- Acceptance: unit test scans rendered DOM for forbidden class bundles (`text-success` on `system_confirmed`).

### T4.4 — TypeScript union update

- `frontend/src/types/compliance.ts` — extend `HitlStatus` to `"auto_approved" | "system_confirmed" | "needs_review" | "user_approved" | "user_rejected" | "user_modified" | "unknown"`.
- Update consumers.
- Acceptance: `npm run typecheck` green.

### T4.5 — Normalise reader

- `frontend/src/stores/compliance-store.ts` — normalise `finding.hitl_status` on ingest: unknown string → `"unknown"` (never `"auto_approved"`).
- Acceptance: unit test loads a fixture with `hitl_status: undefined` and asserts the store emits `"unknown"`.

### T4.6 — Fetch client echoes trace

- New `frontend/src/lib/observability.ts` — wraps `fetch` to (a) send `traceparent` if we have one in context, (b) read the response `X-Request-Id` and stash it in a store for display in DevTools / dev UI footer.
- Swap all compliance + BMR `fetch(...)` sites to this wrapper (existing wrapper can be a small rename).
- Acceptance: Network panel shows `traceparent` on outbound requests.

---

## Track 5 — Tests & benchmark

### T5.1 — Overhead benchmark

- `backend/tests/observability/test_overhead_benchmark.py` — benchmarks a minimal FastAPI route with and without middleware, asserts Δp95 ≤ 5 ms.
- Marked `@pytest.mark.slow`; runs in CI opt-in.

### T5.2 — Trace propagation integration test

- Spin up the whole FastAPI app in a test, send a request with a known `traceparent`, tail the log capture, assert every record shares the `trace_id`.

### T5.3 — Metrics catalogue drift test

- Asserts `__all__` in `metrics.py` equals the set listed in `contracts/metrics.md`.

### T5.4 — Events catalogue drift test

- Scans source for `event="..."` kwargs, collects them, diffs against `contracts/events.md`.

### T5.5 — HITL display contract test (frontend)

- Unit-tests the badge component for each wire value in `contracts/hitl-display-contract.md`; asserts label, tooltip, palette.

### T5.6 — Finding-attribution invariant test

- Constructs a report with cross-agent duplicates in both dedup modes; asserts counts match the global list in each mode.

### T5.7 — No `auto_approved` fallback anywhere

- Scans backend + frontend source for the literal `"auto_approved"` as a fallback in a ternary / `get(...)` / `??` and fails if found.

---

## Sequencing

- Track 1 → (parallel) Track 2, Track 3, Track 4 → Track 5.
- Total effort estimate: ~2 engineer-days for Track 1, ~1 day each for Tracks 2 / 3 / 4, ~1 day for Track 5. Can parallelise 3+4 with a frontend + backend engineer.

## Exit criteria (gate)

- All Success Criteria in `spec.md` pass (SC-001 through SC-007).
- All FR-001 through FR-017 covered by at least one acceptance test.
- `checklists/requirements.md` is fully ticked.
- Ruff + pyright + npm typecheck clean.
- Overhead benchmark within NFR-001 budget.

---

## Coverage matrix

Every FR has at least one owning task; every task states which FR(s) and SC(s) it advances. Produced during the analyze pass; drift-tested against the spec by a pre-merge grep.

### FR → owning task(s)

| FR | Owning tasks | Primary SC |
|---|---|---|
| FR-001 (`traceparent` + `X-Request-Id`) | T1.4, T1.8 | SC-001 |
| FR-002 (trace across threads) | T1.3, T1.4, T5.2 | SC-001 |
| FR-003 (structured log schema) | T1.5 | SC-001 |
| FR-004 (`/metrics` endpoint) | T1.9 | SC-002 |
| FR-005 (named metric set) | T1.7, T5.3 | SC-002 |
| FR-006 (business context binding) | T1.3, T2.1, T2.3, T2.4 | SC-001 |
| FR-007 (fail-open observability) | T1.8, T5.1 | SC-006 |
| FR-008 (no PII in logs) | T1.6 | — |
| FR-009 (health endpoints) | T1.9 | — |
| FR-010 (BMR WS trace_id) | T2.2 | SC-001 |
| FR-011 (label rename) | T4.3, T4.4, T5.5 | SC-003 |
| FR-012 (neutral palette for model-confirmed) | T4.1, T4.3, T5.5 | SC-003 |
| FR-013 (explicit `unknown` state) | T3.1, T4.2, T4.5, T5.7 | SC-004 |
| FR-014 (dedup attribution preserved) | T3.2, T3.3, T5.6 | SC-005 |
| FR-015 (dedup observability) | T3.2, T3.4 | SC-005 |
| FR-016 (`/metrics`/`/health*` outside auth) | T1.9, T1.10 | SC-002 |
| FR-017 (no scoring change) | T3.1, T3.2 (regression test only) | — |

### NFR → owning task(s)

| NFR | Owning tasks |
|---|---|
| NFR-001 (p95 ≤ 5 ms overhead) | T5.1 |
| NFR-002 (cardinality ceiling) | T1.7, T5.3 |
| NFR-003 (no FastAPI imports in domain) | T1.2 (package boundary), T2.* (no deps), T5.* (CI grep) |
| NFR-004 (test isolation) | T1.7 (`reset_for_tests`), T5.* (fixtures) |
| NFR-005 (docs current) | T5.3, T5.4 |

### SC → task chain

- **SC-001** (single trace_id correlates whole request): T1.3 + T1.4 + T1.5 + T1.8 → T2.1, T2.2, T2.3, T2.4 → T5.2.
- **SC-002** (`/metrics` usable): T1.7 + T1.9 → T5.3.
- **SC-003** (severity dominates, no success-green on model-confirmed): T4.1 + T4.3 → T5.5.
- **SC-004** (no `auto_approved` fallback): T3.1 + T4.2 + T4.5 + T5.7.
- **SC-005** (per-agent count = filtered global): T3.2 + T3.3 → T5.6.
- **SC-006** (overhead ≤ 5 ms): T5.1.
- **SC-007** (OTLP migration = one-line change): T1.4 + T1.5 (ids + processors are OTEL-shape) → `quickstart.md §6` is the demonstration.

### Orphan check (run as a test in T5.3 / T5.4)

- Every FR in `spec.md` must appear in the FR column above.
- Every task in `tasks.md` must appear in at least one row (FR or NFR or SC).
- Fail the drift test if a new FR / task is added without updating this table.
