# Quickstart — Observability & Finding-Semantics

Local walkthrough covering: (1) running the observability-enabled backend, (2) correlating one request end-to-end, (3) scraping `/metrics`, (4) inspecting the finding-semantics fix on the existing pilot doc, (5) what to change to swap in an OTLP exporter later.

Prerequisite: Feature 006 merged; backend and frontend run locally as usual.

---

## 1. Run the backend with observability on

```shell
cd backend
export AT_BMR__API_TOKEN=""             # keep PR #2 auth gate off for local testing
export AT_OBS__LOG_MODE=dev              # colourised stdout; use "json" for prod
export AT_OBS__LOG_LEVEL=INFO
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8100
```

On startup you should see one line:

```
10:15:30.000 INFO  trace.request.started source=minted route=lifespan
```

Note the `trace_id=...`. Every subsequent line during startup inherits this trace id because lifespan is wrapped in a synthetic span — tests for this behaviour live in `backend/tests/observability/test_lifespan_trace.py`.

---

## 2. Correlate one request end-to-end

Send a request with a known `traceparent`:

```shell
curl -i -X POST http://localhost:8100/api/compliance/f9f7e1b6-d7a3-415c-8275-795ec0c69888/run \
  -H 'Content-Type: application/json' \
  -H 'X-Actor-Id: you@example.com' \
  -H 'traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01' \
  -d '{"enabled_agents":["alcoa","gmp"]}'
```

Response headers include:

```
HTTP/1.1 202 Accepted
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-<handler_span_id>-01
X-Request-Id: 4bf92f3577b34da6a3ce929d0e0e4736
Access-Control-Expose-Headers: traceparent, X-Request-Id, tracestate
```

Tail the backend log and grep:

```shell
./logs.sh | grep 4bf92f3577b34da6a3ce929d0e0e4736
```

You'll see the whole pipeline in order:

```
trace.request.started       route=POST /api/compliance/{doc_id}/run source=inbound
compliance.run.started      doc_id=f9f7e1b6-… enabled_agents=[alcoa,gmp] total_rules=113
compliance.agent.started    agent=alcoa doc_id=f9f7e1b6-…
llm.call.started            model=gpt-4o purpose=evaluator
llm.call.completed          model=gpt-4o duration_ms=2341 prompt_tokens=7821 completion_tokens=412
compliance.rule.evaluated   agent=alcoa rule_id=alcoa.attributable.signature status=compliant
…
compliance.agent.completed  agent=alcoa findings_count=14 duration_ms=41230
compliance.agent.started    agent=gmp …
…
compliance.run.completed    overall_score=53.3 total_findings=19 duration_ms=88441
trace.request.finished      status=202 duration_ms=88444
```

Every line carries the same `trace_id`. Every worker thread that ran a per-agent evaluation also emitted under this trace id — verified because `concurrent.futures.ThreadPoolExecutor` submissions in `compliance_graph.py` use `observability.tracing.submit_with_context`.

---

## 3. Scrape `/metrics`

```shell
curl -s http://localhost:8100/metrics | head -50
```

Output (truncated):

```
# HELP http_requests_total HTTP requests by route + status class.
# TYPE http_requests_total counter
http_requests_total{method="POST",route="/api/compliance/{doc_id}/run",status_class="2xx"} 1
http_requests_total{method="GET",route="/api/bmr/runs/{run_id}/report",status_class="2xx"} 4

# HELP compliance_findings_total Findings emitted, by agent / status / severity / hitl_status.
# TYPE compliance_findings_total counter
compliance_findings_total{agent="alcoa",status="non_compliant",severity="major",hitl_status="auto_approved"} 9
compliance_findings_total{agent="alcoa",status="non_compliant",severity="critical",hitl_status="needs_review"} 3
compliance_findings_total{agent="gmp",status="non_compliant",severity="major",hitl_status="auto_approved"} 5
…

# HELP compliance_dedup_merges_total Cross-agent dedup events.
# TYPE compliance_dedup_merges_total counter
compliance_dedup_merges_total{mode="cross_agent_preserve"} 0
```

Point Prometheus at the service (example `scrape_configs` entry):

```yaml
- job_name: docs-digitization
  scrape_interval: 10s
  static_configs:
    - targets: ['localhost:8100']
```

No auth, no TLS for local dev. The `/metrics` endpoint is explicitly outside the `require_actor` gate.

---

## 4. Verify the finding-semantics fix on the pilot doc

Open the frontend on the same document:

```
http://localhost:3100/compliance?doc=f9f7e1b6-d7a3-415c-8275-795ec0c69888
```

What changed:

- Category `alcoa/legible` still scores 0/100. Its finding rows still exist.
- The HITL badge on those rows now reads **"System-confirmed"** (not "Auto-approved").
- The badge palette is **neutral grey**, not success green. Severity (critical / major / minor) drives the row's dominant colour.
- Hover a badge — tooltip reads *"Model-only review — high confidence, no reviewer needed."*

Developer console — inspect a badge element. Its class list no longer contains `text-success` / `border-success/20` / `bg-success/5`. These classes are asserted absent by `frontend/tests/compliance/findings-table.test.tsx`.

---

## 5. Trigger a dedup merge (synthetic)

The pilot dataset does not exercise cross-agent dedup. A synthetic test fixture does:

```shell
cd backend
uv run pytest tests/compliance/test_dedup_attribution.py -v
```

One test constructs two agents both producing a finding for the same `rule_id`. It asserts:

1. `sum(ar.total_findings for ar in report.agent_reports) == sum(1 for f in report.findings if f.agent)` — no divergence.
2. `compliance_dedup_merges_total{mode="cross_agent_preserve"}` has incremented.
3. A `compliance.finding.deduped` log event was emitted with `winner_agent` and `dropped_agents` fields.

---

## 6. Swap in an OpenTelemetry OTLP exporter (future)

When the team decides to ship traces to a backend, this is the *single* code change:

```python
# backend/app/observability/logging.py

def configure():
    ...
    # NEW — enable OTLP export
    if os.getenv("AT_OBS__OTLP_ENDPOINT"):
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=os.getenv("AT_OBS__OTLP_ENDPOINT"))))
        trace.set_tracer_provider(provider)
```

Domain code is unchanged. `@traced("bmr.stage.ingest")` now also produces an OTLP span in addition to a structured log line. `traceparent` in and out is already the same shape an OTLP collector expects.

Env vars:

```shell
export AT_OBS__OTLP_ENDPOINT=http://localhost:4317
```

Start Jaeger / Tempo locally, re-run the compliance request, and the trace shows up in the UI — one waterfall diagram per request, every stage and agent as a span.

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Log lines missing `trace_id` | Caller used the stdlib `logging` directly and bypassed the stdlib→structlog bridge | Import `get_logger` from `app.observability` instead |
| A log line shows `trace_id` from the PREVIOUS request | Context leak — a background task held a reference past request end | Wrap the task with `copy_context().run(fn)` |
| `/metrics` returns "duplicate timeseries" | A metric was registered twice (e.g. in a module reloaded under `--reload`) | Use `registry=REGISTRY` on every metric; rely on the process-wide registry |
| Frontend shows `Auto-approved` instead of `System-confirmed` | Stale bundle or cache | Rebuild frontend; hard-reload |
| `traceparent` header dropped by a reverse proxy | Proxy strips unknown headers | Whitelist `traceparent`, `tracestate`, `X-Request-Id` in the proxy config |

---

## 8. Rollout checklist (the team actually turning this on)

1. Merge the Feature 006 PR.
2. Confirm `/metrics` is reachable from the Prometheus scrape target.
3. Add a dashboard with at minimum: request rate, p95 latency by route, finding volume by agent/severity/HITL state, error rate.
4. Add alerts: 5xx rate > 1 %/5 min; `compliance_runs_total{status="failed"}` rate > 0; `errors_total{kind="LogRedaction"}` rate > 0 (signals caller misuse).
5. Teach the on-call team the `grep trace_id=<hex>` pattern.
6. Later: add OTLP exporter per §6.
