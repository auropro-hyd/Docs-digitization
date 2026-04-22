# Contract — REST API (v2)

**Spec**: 001-bmr-audit-pipeline  **Revision**: v2 (2026-04-17) — narrows legibility HITL endpoint to upload/proceed only; adds structured-resolution + feedback endpoints.

All endpoints below are served under `/api/v1/bmr-audit/` by `backend/app/api/routers/bmr_audit.py`.

Cross-cutting:
- **Auth**: existing session auth; requests without a valid user are rejected `401`.
- **Actor header**: every mutating request MUST be authenticated; `actor` is always the
  authenticated user (never client-supplied).
- **Timestamps**: server-assigned; client-supplied timestamps are ignored.
- **Correlation-Id**: optional `X-Correlation-Id` header is propagated into audit-trail
  entries.
- **Error schema**: `{ code: str, message: str, field_errors?: [...], details?: {...} }`
  with standard HTTP status codes.
- **Rate limiting**: correction/rerun endpoints are rate-limited per user (default 60/min).

---

## 1. Run lifecycle

### 1.1 `POST /runs`

Start a new BMR audit run.

Body: `{ package_id, manifest_id?, mode?: "leverage" | "process_replication", mode_trigger_reason? }`

Response `201`: `{ run_id, mode, rule_set_version, current_stage, status }`.

Validation:
- `mode_trigger_reason` required iff `mode = process_replication`.
- `package_id` must exist and be ready for audit (Spec 002's package status = `READY`).

### 1.2 `GET /runs/{run_id}`

Return the run's summary.

Response `200`: `{ run_id, package_id, manifest_id, mode, rule_set_version, current_stage,
status, started_at, completed_at?, started_by, stage_states: [...], counts: {
findings_total, findings_critical, findings_major, findings_minor, findings_observation,
findings_unresolved_critical_major } }`.

### 1.3 `GET /runs/{run_id}/stage-states`

Per-stage state + sub-phases.

Response `200`: `[ { stage, status, sub_phase?, entered_at, exited_at?, gate_result,
error_summary? } ]`.

### 1.4 `POST /runs/{run_id}/cancel`

Cancel an in-progress run.

Response `200`: `{ status: "CANCELLED" }`.

---

## 2. Legibility HITL (narrow)

### 2.1 `GET /runs/{run_id}/legibility/queue`

List pages awaiting legibility HITL.

Response `200`: `[ { document_id, page_number, verdict: "FAIL" | "MARGINAL", confidence,
reasons: [...], page_thumbnail_url } ]`.

### 2.2 `POST /runs/{run_id}/legibility/pages/{document_id}/{page_number}/reupload`

Replace a page with a re-uploaded scan. Multipart.

Body: `file=<image/pdf>`, optional `note`.

Response `202`: `{ hitl_action_id, status: "RE_LEGIBILITY_RUNNING" }`.

Server re-runs `legibility_check.v1` on the replacement. Downstream stages re-enter for that
page only.

### 2.3 `POST /runs/{run_id}/legibility/pages/{document_id}/{page_number}/proceed`

Accept the page as-is and continue.

Body: `{ note? }`.

Response `200`: `{ hitl_action_id, status: "PROCEEDING" }`.

**Contract**: these are the ONLY finding-related actions offered at this touchpoint. The
router MUST reject any request body or action hint resembling `confirm`, `dismiss`, or
`correct` with HTTP `400` (per Constitution IV).

---

## 3. Findings

### 3.1 `GET /runs/{run_id}/findings`

Return consolidated findings. Default grouping is by BPCR step.

Query params: `group_by=bpcr_step|document|severity` (default `bpcr_step`),
`include_retracted=false`, `severity_min=OBSERVATION`.

Response `200`: `[ { group_key, findings: [ <Finding> ] } ]` where `<Finding>` is the full
data-model §1.3 shape including `source` and `source_finding_ids`.

### 3.2 `GET /runs/{run_id}/findings/{logical_id}`

Full finding detail with evidence pointers.

Response `200`: `{ ...<Finding>, evidence_links: [{ page_image_url, text_span_url? }] }`.

### 3.3 `GET /runs/{run_id}/findings/{logical_id}/revisions`

All revisions of a finding.

Response `200`: `[ <Finding>, ... ]`.

---

## 4. Final checkpoint — Structured Resolutions

### 4.1 `POST /runs/{run_id}/findings/{logical_id}/resolutions`

Record a resolution on a finding.

Body:
```json
{
  "finding_revision": 1,
  "action": "CONFIRM" | "DISMISS" | "CORRECT",
  "reason_type": "OCR_MISREAD" | "ACCEPTABLE_VARIANCE" | "DUPLICATE_FINDING" | "OUT_OF_SCOPE" | "RULE_MISCONFIGURED" | "OTHER",
  "observed_value_on_document": "...",
  "note": "...",
  "correction": {                          // required if action == "CORRECT"
    "target": { "document_id": "...", "page_number": 7, "field_path": "raw_material[2].weight_kg" },
    "new_value": "12.5",
    "value_kind": "quantity_kg"
  }
}
```

Validation:
- `action ∈ { DISMISS, CORRECT }` ⇒ `reason_type` required.
- `action = CORRECT` ⇒ `correction` required.
- `action = DISMISS` with `reason_type ∈ { OCR_MISREAD, ACCEPTABLE_VARIANCE }` ⇒
  `observed_value_on_document` required.
- `finding_revision` must match the current latest revision; stale revisions rejected `409`.

Response `201`: `{ resolution_id, correction_id?, feedback_sample_id, plan_id?, audit_trail_seq_no }`.

Side effects:
- Writes `StructuredResolution`, `HITLAction`, `AuditTrailEntry`, `FeedbackSample`.
- If `action = CORRECT`, also generates a `ReExecutionPlan` in `PROPOSED` state and returns
  `plan_id`.

### 4.2 `GET /runs/{run_id}/rerun-plans/{plan_id}`

Return the generated plan for reviewer inspection.

Response `200`: `{ plan_id, rules_to_reeval: [...], capabilities_to_invoke: [...],
estimated_runtime_seconds, status }`.

### 4.3 `POST /runs/{run_id}/rerun-plans/{plan_id}/confirm`

Confirm and execute.

Response `202`: `{ status: "EXECUTING" }`.

### 4.4 `POST /runs/{run_id}/rerun-plans/{plan_id}/cancel`

Response `200`: `{ status: "CANCELLED" }`.

### 4.5 `GET /runs/{run_id}/resolutions/stale`

List resolutions made stale by re-runs that the reviewer has not yet re-actioned.

Response `200`: `[ <StructuredResolution with superseded_by set> ]`.

---

## 5. Export

### 5.1 `POST /runs/{run_id}/export`

Produce the final PDF (and optional CSV of findings). Blocked until all Critical+Major
findings have non-stale resolutions (FR-004).

Body: `{ format: "pdf" | "pdf+csv" }`.

Response `201`: `{ export_id, download_url }`.

Errors:
- `409` `EXPORT_BLOCKED_UNRESOLVED` with `{ unresolved_count }`.

### 5.2 `GET /runs/{run_id}/exports/{export_id}`

Response `200`: `{ export_id, format, download_url, created_at, checksum }`.

---

## 6. Audit trail & feedback corpus

### 6.1 `GET /runs/{run_id}/audit-trail`

Return the append-only audit trail.

Query: `since_seq_no?`, `event_type?`, `limit=500`.

Response `200`: `[ <AuditTrailEntry> ]`.

### 6.2 `GET /runs/{run_id}/feedback-samples`

Scoped view of the feedback corpus for this run. Global corpus access is governed separately
(Spec 004 / Spec 005).

Response `200`: `[ <FeedbackSample> ]`.

---

## 7. Mode control

### 7.1 `POST /runs/{run_id}/switch-mode`

Switch from `leverage` to `process_replication` (v1 accepts this one direction only).

Body: `{ target_mode: "process_replication", trigger_reason: str }`.

Response `200`: `{ mode: "process_replication", mode_trigger_reason, switched_at }`.

Only permitted while run status is `AWAITING_FINAL_CHECKPOINT` or earlier. Switching
produces a fresh run tree whose `predecessor_run_id` points at the original (§1.1).

---

## 8. OpenAPI

An OpenAPI 3.1 specification will be generated from the FastAPI router; `tasks.md` will
include the generation step and a contract test that diffs the generated spec against the
committed one.
