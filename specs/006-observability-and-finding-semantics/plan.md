# Implementation Plan: Observability & Finding-Semantics Hardening

**Branch**: `006-observability-and-finding-semantics` | **Date**: 2026-04-22 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/006-observability-and-finding-semantics/spec.md`

## Summary

Ship a self-contained `app/observability/` package that provides: (1) W3C Trace Context propagation via `traceparent` and `X-Request-Id` on every HTTP response, (2) `contextvars`-backed structured logging via `structlog` with JSON in prod / colourised text in dev, (3) a closed, named set of Prometheus metrics exposed at `GET /metrics`, (4) `@traced` decorators and `bind_context()` helpers that the domain modules call through narrow `Protocol` interfaces so they never import FastAPI. Simultaneously, fix the compliance-report semantic bugs surfaced during PR #2 review: relabel/restyle `auto_approved`, replace the inverted default with explicit `unknown`, and fix cross-agent dedup so per-agent `total_findings` always matches the globally visible attribution.

The design is OpenTelemetry-shape-compatible from day one without pulling in the OTEL SDK — swapping to OTLP later is a config-only change.

## Technical Context

**Language/Version**: Python 3.13 (backend), TypeScript + Next.js 15 (frontend)
**Primary Dependencies**: FastAPI, Pydantic v2, structlog, prometheus-client, opentelemetry-api (not SDK), existing LangGraph / Pydantic domain models
**Storage**: no new persistent storage (observability state is in-process / stdout / /metrics scrape)
**Testing**: pytest (backend), vitest / jest (frontend), one benchmark under `backend/tests/benchmark/observability_overhead.py`
**Target Platform**: Linux container behind uvicorn, single-worker for v0; multi-worker safe
**Project Type**: web-service (existing `backend/` + `frontend/` split)
**Performance Goals**: `/metrics` p95 ≤ 200 ms at 2× concurrent scrape @ 10 s; observability overhead on request p95 ≤ 5 ms
**Constraints**: no unbounded metric cardinality; no PII / raw content in logs; no FastAPI imports in `app/bmr/` or `app/compliance/`; fail-open on observability faults
**Scale/Scope**: 1 backend service; ≤ 500 req/min in pilot; 5 compliance agents; 33 findings per report p50, 100 p99

## Constitution Check

Reference: `.specify/memory/constitution.md` (v1.1.0).

- [x] **I. Leverage-first**: Observability reuses existing infrastructure (`app/bmr/events/`, structured storage, existing middleware chain); it does not replace any subsystem. Compliance fixes reuse existing evaluator / projection code — only semantics change.
- [x] **II. 5-stage soft gates + parallel compliance**: No change to the stage topology. Observability is cross-cutting; it hooks into stage boundaries via `bind_context()` without re-ordering them. Parallel ALCOA/GMP within the Compliance stage is preserved.
- [x] **III. Capability-first**: Each new observability surface is a separate, independently callable capability — `log()`, `metric.observe()`, `tracer.span()`, `bind_context()` — not a fat "Observability" god object.
- [x] **IV. Single final checkpoint & selective re-run**: N/A — observability emits no findings; semantic fix preserves existing re-run scope and attribution (indeed, FR-014 fixes a latent attribution bug that would have corrupted re-run scope).
- [x] **V. Evidence-bound findings**: N/A for observability; preserved for compliance — every persisted finding keeps its full evidence fields; the rename affects display only (FR-017).
- [x] **VI. Configurable framework**: Log level, log format (JSON vs dev), metrics endpoint exposure, and PII-redaction limits are configured via `AT_OBS__*` env vars, not Python constants. No client-specific wiring.
- [x] **VII. Existing framework is the backbone**: Domain packages (`app/bmr/`, `app/compliance/`) do not import FastAPI or Prometheus. They accept a `Logger` / `Tracer` / `MetricRegistry` via constructor or module-level injector. Existing tests keep passing.
- [x] **VIII. ALCOA+ audit trail**: Observability adds attribution (`actor_id`, `trace_id`, monotonic timestamps) to every log line, reinforcing ALCOA. No raw values are redacted away; PII redaction applies only to large free-text blobs.
- [x] **IX. Rule-as-data**: N/A for observability. For the finding-semantics fix, the display mapping lives in frontend config (`HITL_CONFIG`) and backend config (`reporting_config.py` extension) — never hardcoded in domain code.

No violations. Nothing to add to Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/006-observability-and-finding-semantics/
├── plan.md                      # This file
├── spec.md
├── research.md                  # Decisions: OTEL-shape vs OTEL-SDK; structlog vs stdlib; label set; fix vs rewrite dedup
├── data-model.md                # TraceContext, RequestScope, LogEvent, Metric, HITLDisplayState entities
├── quickstart.md                # Local dev walkthrough + OTLP migration example
├── contracts/
│   ├── events.md                # Canonical list of structured log event names + fields
│   ├── metrics.md               # Canonical list of metric names + label sets
│   ├── trace-header-contract.md # traceparent parsing + emission rules
│   └── hitl-display-contract.md # wire keys, display strings, allowed palettes
├── checklists/
│   └── requirements.md          # pre-merge gate
└── tasks.md                     # execution breakdown (produced by /speckit.tasks)
```

### Source Code

```text
backend/
├── app/
│   ├── observability/                # NEW — cross-cutting, no FastAPI imports outside middleware.py
│   │   ├── __init__.py               # re-exports: get_logger, bind_context, traced, metric
│   │   ├── context.py                # contextvars: TRACE_CTX, REQUEST_CTX; bind / snapshot helpers
│   │   ├── logging.py                # structlog config; processors (ctx inject, redact, render)
│   │   ├── metrics.py                # prometheus Registry, named Counters/Histograms/Gauges
│   │   ├── middleware.py             # FastAPI middleware: traceparent parse/emit + request metrics
│   │   ├── tracing.py                # @traced decorator + span() context manager (OTEL-shape ids)
│   │   ├── redaction.py              # filter for oversized/base64 payloads in logs
│   │   ├── protocols.py              # Logger, Tracer, MetricRegistry Protocol classes
│   │   └── health.py                 # /health, /health/ready, /metrics route registrations
│   ├── api/
│   │   └── routes/
│   │       └── compliance.py         # CHANGED — drop `f.get("hitl_status", "auto_approved")` fallback; use Logger
│   ├── bmr/
│   │   ├── workflow/
│   │   │   ├── service.py            # CHANGED — bind_context(run_id, doc_id) at stage entry
│   │   │   ├── stages.py             # CHANGED — @traced("bmr.stage.<name>") on each stage fn
│   │   │   └── extractor.py          # CHANGED — @traced, logger.info events
│   │   ├── events/
│   │   │   └── __init__.py           # CHANGED — attach trace_id to every event envelope
│   │   └── hitl/
│   │       └── service.py            # CHANGED — bind_context(run_id, finding_id) on HITL ops
│   ├── compliance/
│   │   ├── evaluator.py              # CHANGED — `_deduplicate_findings` attribution-preserving mode
│   │   └── orchestrator.py           # CHANGED — bind_context(doc_id, agent) on agent entry
│   └── main.py                       # CHANGED — register middleware + health router; remove ad-hoc logging.basicConfig
├── tests/
│   ├── observability/                # NEW
│   │   ├── test_trace_propagation.py
│   │   ├── test_metrics_catalogue.py
│   │   ├── test_context_survives_executor.py
│   │   ├── test_redaction.py
│   │   ├── test_health_endpoints.py
│   │   └── test_overhead_benchmark.py    # NFR-001 gate, skipped by default
│   └── compliance/
│       ├── test_finding_semantics.py     # FR-011..013 (server)
│       └── test_dedup_attribution.py     # FR-014, FR-015

frontend/
├── src/
│   ├── components/
│   │   └── compliance/
│   │       ├── findings-table.tsx     # CHANGED — HITL_CONFIG rename + default removal + palette
│   │       └── hitl-badge.tsx         # NEW (extracted) — so tests can target it in isolation
│   ├── types/
│   │   └── compliance.ts              # CHANGED — HitlStatus union adds "unknown"; "system_confirmed" alias
│   ├── lib/
│   │   └── observability.ts           # NEW — injects traceparent on every fetch
│   └── stores/
│       └── compliance-store.ts        # CHANGED — read through an explicit normaliser that coerces missing hitl_status → "unknown"
└── tests/
    └── compliance/
        └── findings-table.test.tsx    # NEW — SC-003 gate
```

**Structure Decision**: Web application (Option 2) — preserve the existing `backend/` + `frontend/` split. All new backend code lives under `backend/app/observability/` for clean SoC; domain modules touch observability only through injected `Protocol` interfaces from that package. Frontend changes are narrowly scoped to the findings-table display component, its extracted badge subcomponent, and a single fetch-client utility.

## Implementation Phasing

### Phase 0 — Research and decisions (`research.md`)

Document the binding choices before code:

1. W3C Trace Context (`traceparent`) vs custom `X-Request-Id`-only → use both (header primary, human id echoed); compatibility beats elegance.
2. `structlog` vs stdlib `logging` vs bespoke → `structlog` with a stdlib bridge so `app.X.Y` loggers keep working unchanged.
3. OpenTelemetry API + Prometheus client vs full OTEL SDK → API + Prometheus now, SDK later; the shape is OTEL-compatible so no rewrite.
4. Metric label set — enumerate what's allowed and what's banned (cardinality budget).
5. Fix `_deduplicate_findings` vs rewrite dedup path — fix in place, default mode `preserve_attribution`, opt-out via report field.
6. Relabel `auto_approved` → `system_confirmed` only in *display*; keep the wire value to avoid breaking persisted reports.

### Phase 1 — Data model & contracts (`data-model.md`, `contracts/`)

- Entities: `TraceContext`, `RequestScope`, `LogEvent`, `Metric`, `HITLDisplayState` (fields, lifecycles, invariants).
- `contracts/trace-header-contract.md`: exact `traceparent` parse grammar, emission rules, malformed-header behaviour.
- `contracts/events.md`: canonical log event names with required fields. One section per domain (`compliance.*`, `bmr.*`, `hitl.*`, `http.*`, `error.*`, `trace.*`).
- `contracts/metrics.md`: every metric name, type (counter/histogram/gauge), unit, labels, cardinality budget, and prose description.
- `contracts/hitl-display-contract.md`: allowed wire values, display labels, allowed palettes, and the exact tuple `(wire_value, display_label, badge_palette, tooltip)` that the frontend must render.

### Phase 2 — Implementation (covered in tasks.md)

Five parallel-safe tracks:

1. **Foundation**: `app/observability/*`, middleware, `/metrics`, `/health*`. No domain code touched.
2. **Wiring**: `bind_context` / `@traced` additions in BMR + Compliance code (thin, mostly one line per stage).
3. **Finding-semantics server**: compliance.py default removal, evaluator dedup fix, metric for dedup merges.
4. **Finding-semantics client**: `HITL_CONFIG` restyle + rename, `hitl-badge.tsx` extraction, fetch client trace injection.
5. **Tests**: contract + unit + integration + overhead benchmark.

Each track has clear inputs/outputs and can be reviewed + merged independently if we split the PR.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| structlog integration breaks existing `logging.basicConfig` users | Medium | Low | Keep a stdlib→structlog bridge; verify `logging.getLogger("azure.core.*").setLevel(WARNING)` in main.py still works. |
| `contextvars` lost across `ThreadPoolExecutor` | High | High | Explicit `copy_context()` in the executor wrapper; unit test `test_context_survives_executor.py`. |
| Metric cardinality blowup from an accidental `doc_id` label | Medium | High | Static analysis: a unit test imports `metrics.py` and asserts every label is in the cardinality-budget whitelist. |
| Front-end restyle introduces regressions in other compliance screens | Medium | Medium | Extract `HITLBadge` to its own component used everywhere; write a visual snapshot test; scan all consumers. |
| PII leakage through log payloads | Medium | High | Redaction filter rejects oversized / base64-looking values; fuzz test in `test_redaction.py`. |
| Performance regression from JSON logging on every line | Low | Medium | Benchmark in `test_overhead_benchmark.py` enforcing NFR-001; run on CI (opt-in). |
| Dedup change breaks existing compliance snapshots | Low | Medium | Default mode is attribution-preserving; add a `report.dedup_mode` field; legacy reports resolve to the legacy path until rewritten. |

## Release Strategy

- Single PR on a new branch `006-observability-and-finding-semantics` (do **not** bundle with the BMR PR — observability cuts across every route and merits focused review).
- Roll out behind an `AT_OBS__ENABLED=true` env default; set to `false` in tests that need the un-instrumented baseline for the overhead benchmark.
- Frontend + backend changes ship together — the display rename is meaningless without both.
- After merge: publish the metric catalogue internally; invite dashboard authors to build on top.

## Complexity Tracking

No violations. All Constitution gates are green.
