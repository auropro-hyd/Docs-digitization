# Contract — Structured Log Events

**Satisfies**: FR-003 (structured log schema), FR-006 (business context binding), FR-008 (redaction), FR-010 (WS trace_id).

Every `logger.info/warn/error/exception` call in the codebase passes `event=<name>` where `<name>` is drawn from the closed catalogue below. Ad-hoc names are forbidden; a lint-style test parses source files and fails when it finds log calls without an explicit `event=` kwarg.

Event names follow the convention: `<domain>.<subject>.<action>` — lower-snake, dotted. Domains are `trace`, `http`, `compliance`, `bmr`, `hitl`, `llm`, `error`.

---

## trace.*

| Event | Level | Required fields | Emitted by |
|---|---|---|---|
| `trace.request.started` | info | `route`, `method`, `source` (`inbound` / `minted`) | middleware |
| `trace.request.finished` | info | `route`, `method`, `status`, `duration_ms` | middleware |
| `trace.malformed_header` | warn | `raw_header` (truncated to 128 chars), `reason` | middleware |
| `trace.header_mismatch` | warn | `traceparent_value`, `x_request_id_value` | middleware |
| `trace.context.lost` | warn | `executor`, `recovered_trace_id` | executor wrapper |

## http.*

`http.*` events are emitted by the same middleware as `trace.*` but describe the transport, not the correlation. Usually `trace.request.finished` is enough; these are available for diagnostic granularity.

| Event | Level | Required fields |
|---|---|---|
| `http.auth.denied` | warn | `reason` (`missing_actor` / `malformed_actor` / `bad_token`) |
| `http.rate_limited` | warn | `limit`, `retry_after_s` |

## compliance.*

| Event | Level | Required fields |
|---|---|---|
| `compliance.run.started` | info | `doc_id`, `enabled_agents`, `total_rules` |
| `compliance.run.completed` | info | `doc_id`, `duration_ms`, `overall_score`, `total_findings` |
| `compliance.run.failed` | error | `doc_id`, `stage`, `error.kind`, `error.msg` |
| `compliance.agent.started` | info | `agent`, `doc_id`, `total_rules` |
| `compliance.agent.completed` | info | `agent`, `doc_id`, `duration_ms`, `findings_count` |
| `compliance.agent.skipped` | info | `agent`, `reason` |
| `compliance.agent.failed` | error | `agent`, `error.kind`, `error.msg` |
| `compliance.finding.emitted` | debug | `agent`, `rule_id`, `status`, `severity`, `hitl_status`, `confidence` |
| `compliance.finding.deduped` | info | `rule_id`, `winner_agent`, `dropped_agents`, `mode` |
| `compliance.rule.evaluated` | debug | `agent`, `rule_id`, `status`, `page_numbers`, `duration_ms` |
| `compliance.rule.errored` | error | `agent`, `rule_id`, `error.kind`, `error.msg` |

## bmr.*

| Event | Level | Required fields |
|---|---|---|
| `bmr.run.started` | info | `run_id`, `package_id`, `rules_dir` |
| `bmr.run.completed` | info | `run_id`, `duration_ms`, `status`, `rules_evaluated`, `findings_count` |
| `bmr.run.failed` | error | `run_id`, `stage`, `error.kind`, `error.msg` |
| `bmr.stage.entered` | info | `run_id`, `stage` |
| `bmr.stage.completed` | info | `run_id`, `stage`, `duration_ms` |
| `bmr.stage.skipped` | info | `run_id`, `stage`, `reason` |
| `bmr.legibility.awaiting_review` | info | `run_id`, `reasons` |
| `bmr.legibility.decided` | info | `run_id`, `action` (`proceed`/`reupload`), `decided_by` |
| `bmr.package.drift_detected` | warn | `run_id`, `expected_hash`, `actual_hash` |
| `bmr.extraction.mismatched_package_id` | error | `run_id`, `expected`, `actual` |
| `bmr.rule.crashed` | error | `run_id`, `rule_id`, `error.kind`, `error.msg` |

## hitl.*

| Event | Level | Required fields |
|---|---|---|
| `hitl.resolution.recorded` | info | `run_id`, `finding_id`, `action`, `reason_type`, `actor_id` |
| `hitl.resolution.superseded` | info | `run_id`, `finding_id`, `previous_resolution_id`, `new_resolution_id` |
| `hitl.correction.started` | info | `run_id`, `finding_id`, `field`, `doc_id`, `page_index` |
| `hitl.correction.applied` | info | `run_id`, `workflow_id`, `affected_rule_ids`, `new_finding_ids` |
| `hitl.correction.failed` | error | `run_id`, `workflow_id`, `error.kind`, `error.msg` |
| `hitl.export.requested` | info | `run_id`, `actor_id` |
| `hitl.export.blocked` | warn | `run_id`, `status` (gate status), `pending_blocking_count` |
| `hitl.export.completed` | info | `run_id`, `revision_id`, `revision_number` |

## llm.*

Covers the OpenAI/Anthropic/Gemini calls made by agents and orchestrators.

| Event | Level | Required fields |
|---|---|---|
| `llm.call.started` | debug | `model`, `purpose` (`evaluator`/`orchestrator`/`exec_summary`) |
| `llm.call.completed` | info | `model`, `purpose`, `duration_ms`, `prompt_tokens`, `completion_tokens`, `total_tokens` |
| `llm.call.retried` | warn | `model`, `attempt`, `reason` |
| `llm.call.failed` | error | `model`, `purpose`, `error.kind`, `error.msg` |

## error.*

`error.unhandled` is the last-resort event for any exception not otherwise named. Caught by the global exception handler; includes stack trace.

| Event | Level | Required fields |
|---|---|---|
| `error.unhandled` | error | `error.kind`, `error.msg`, `error.stack` (truncated to 32 frames), `route` |

---

## Mandatory fields on every record

Independent of the event:

- `ts`, `level`, `logger`, `event`, `msg`
- `trace_id`, `span_id` (may be null if emitted pre-middleware)
- `parent_span_id` (null on root spans)

## Automatic context fields (added by processor)

The `_inject_context_processor` adds these if `REQUEST_SCOPE` has them set:

- `actor_id`, `doc_id`, `run_id`, `stage`, `agent`, `rule_id`

Call sites never pass these explicitly — they're inherited.

---

## Redaction

Applied by the redaction processor before rendering:

- Any field whose string value is > 2 KiB → replaced with `"<redacted: oversized (<N> bytes)>"`.
- Any field whose string value matches the base64/PDF heuristic `^%PDF` or `^[A-Za-z0-9+/=]{2048,}$` → replaced with `"<redacted: binary-like>"`.
- `errors_total{kind="LogRedaction"}` counter is incremented on each redaction.

Exempt fields: `event`, `msg`, `error.stack`, `raw_header` (already truncated to 128 chars).
