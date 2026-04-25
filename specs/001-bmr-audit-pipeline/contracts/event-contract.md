# Contract — WebSocket Events (v2)

**Spec**: 001-bmr-audit-pipeline  **Revision**: v2 (2026-04-17) — 5-stage events; adds synthesis + structured-resolution events; removes SOP + old step-walk events.

WebSocket channel: `ws://…/ws/bmr-audit/{run_id}` served by
`backend/app/api/routers/ws_bmr_audit.py`.

---

## 1. Envelope

Every frame is JSON:

```json
{
  "event_id": "<uuid>",
  "run_id": "<uuid>",
  "seq_no": <int>,
  "schema_version": "1.0",
  "event_type": "<str>",
  "recorded_at": "<iso8601>",
  "payload": { ... }
}
```

`(run_id, seq_no)` is monotonic and gap-free; mirrors the AuditTrailEntry sequence so a
reconnecting client can re-sync from `since_seq_no` via REST §6.1.

---

## 2. Stage events

- `stage.entered` — payload: `{ stage, entered_at }`
- `stage.sub_phase_changed` — payload: `{ stage, sub_phase }` (Compliance only)
- `stage.exited` — payload: `{ stage, exited_at, gate_result, error_summary? }`

---

## 3. Document / page progress events

- `document.ingested` — `{ document_id, role?, page_count }`
- `page.legibility_verdict` — `{ document_id, page_number, verdict, confidence, reasons[] }`
- `page.extraction_complete` — `{ document_id, page_number, fields_extracted }`
- `document.summary_complete` — `{ document_id, summary_kind: "page"|"doc" }`

---

## 4. Finding lifecycle

- `finding.created` — full `<Finding>` payload; `source` indicates `direct` or `synthesised`.
- `finding.updated` — on revision bump; payload includes new revision.
- `finding.retracted` — `{ logical_id, revision, reason: "source_finding_retracted" | "correction" | "rule_reevaluation" }`.

---

## 5. Resolution / correction / rerun events

- `resolution.recorded` — payload: `{ resolution_id, finding_logical_id, action, reason_type?, note? }` (note: `observed_value_on_document` and `system_extracted_value` are NOT emitted on the WS channel, only in REST detail — to keep WS frames small).
- `correction.submitted` — `{ correction_id, target, value_kind, plan_id }`.
- `rerun_plan.proposed` — `{ plan_id, rules_count, scopes_count, estimated_runtime_seconds }`.
- `rerun_plan.confirmed` — `{ plan_id, confirmed_by }`.
- `rerun_plan.completed` — `{ plan_id, findings_changed: int, findings_retracted: int, stale_resolutions: int }`.
- `rerun_plan.cancelled` — `{ plan_id }`.
- `resolution.superseded` — `{ resolution_id, finding_logical_id, reason: "rerun_changed_raw_value" | "source_finding_retracted" }`.

---

## 6. Run lifecycle events

- `run.started` — `{ mode, rule_set_version }`
- `run.awaiting_legibility_hitl` — `{ pending_pages: [...] }`
- `run.awaiting_final_checkpoint` — `{ unresolved_critical_major: int }`
- `run.export_produced` — `{ export_id, download_url }`
- `run.completed` — `{ completed_at }`
- `run.failed` — `{ error_code, error_summary }`
- `run.cancelled` — `{ cancelled_by }`
- `run.mode_switched` — `{ from_mode, to_mode, trigger_reason }`

---

## 7. Delivery guarantees

- **Ordering**: per-run strict ordering by `seq_no`. Inter-event gaps are impossible; if
  detected by the client, the client MUST re-sync from `audit-trail` REST.
- **At-least-once** delivery. Clients MUST dedupe by `event_id`.
- **Reconnection**: clients reconnect with `?since_seq_no=N`; the server replays from the
  audit trail.
- **Backpressure**: server-side per-connection queue cap of 1000 events. Overflow closes the
  socket with code `1013 TRY_AGAIN_LATER`; client reconnects and re-syncs.

---

## 8. Mapping to AuditTrail

Every WS event corresponds 1:1 to an `AuditTrailEntry` with the same `seq_no`. The audit
trail is the authoritative log; WS is the live-update channel.

---

## 9. Events the WS channel MUST NOT emit

- Any `finding.review_requested` mid-pipeline (would imply mid-pipeline finding HITL —
  forbidden by Constitution IV outside `REPORT_AND_RESOLUTION`).
- `sop.*` events (SOP is retired at runtime).
- `step_walk.*` events (stage retired; cross-doc step validation is now a rule, not a
  stage).
