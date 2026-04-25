# WebSocket Event Contract: Audit Report & Finding-Level HITL

**Feature**: 004 | **Version**: v1

Events stream on the existing `/api/v1/bmr/runs/{run_id}/events` WebSocket (Spec 001).
Events in this spec supplement, not replace, Spec 001's pipeline / finding lifecycle
events.

All events share an envelope:
```json
{ "type": "...", "run_id": "run_...", "ts": "2026-04-17T...Z", "payload": {...} }
```

## 1. Resolution events

### `resolution.created`
Emitted after a `StructuredResolution` is persisted.
```json
{
  "finding_id": "fnd_...",
  "resolution_id": "res_...",
  "action": "DISMISS",
  "reason_type": "OCR_MISREAD",
  "actor_id": "user_...",
  "feedback_sample_id": "fbs_..."
}
```

### `resolution.superseded`
Emitted when a prior resolution is invalidated by a correction re-run.
```json
{ "finding_id": "fnd_...", "resolution_id": "res_...", "needs_re_action": true }
```

## 2. Correction events

### `correction.preview_computed`
```json
{
  "correction_id": "cwf_...",
  "finding_id": "fnd_...",
  "rerun_plan": { "rule_ids": [...], "estimated_findings_affected": 3, "estimated_duration_ms": 2800 }
}
```

### `correction.confirmed`
Emitted when the reviewer confirms and rerun begins.
```json
{ "correction_id": "cwf_...", "actor_id": "user_..." }
```

## 3. Re-run events (shared with Spec 001's re-run planner)

### `rerun.planned`
```json
{
  "correction_id": "cwf_...",
  "rule_ids": ["alcoa.accurate.bpcr-raw-material-weight-match"],
  "reason": "correction on (RawMaterialPage, weight_kg)"
}
```

### `rerun.in_progress`
Emitted at start; carries a progress counter.
```json
{ "correction_id": "cwf_...", "rules_total": 1, "rules_done": 0 }
```

### `rerun.completed`
```json
{
  "correction_id": "cwf_...",
  "elapsed_ms": 812,
  "findings_invalidated": ["fnd_..."],
  "findings_added": [],
  "findings_unchanged": 138
}
```

## 4. Report gate / export events

### `report.gate_changed`
Emitted when export gate transitions (e.g., last blocking finding actioned).
```json
{ "status": "ready" }
```

### `report.export_started`
```json
{ "revision_number": 2, "predecessor_id": "rev_..." }
```

### `report.export_completed`
```json
{
  "revision_id": "rev_...",
  "revision_number": 2,
  "pdf_sha256": "sha256:...",
  "bundle_sha256": "sha256:..."
}
```

### `report.export_blocked`
```json
{ "status": "blocked_by_pending_findings", "pending_count": 2 }
```

## 5. Forbidden / out-of-scope events (reaffirming Spec 001 ban)

The following MUST NOT be emitted from within this spec's code paths:

- `finding.review_requested` — review is the final HITL, not a mid-pipeline event
- `sop.*` — no SOP agent in this architecture (Constitution VII)
- `step_walk.*` — legacy term; the pipeline is 5-stage

CI asserts via static analysis that no WebSocket broadcaster in
`app/report/`, `app/resolution/`, `app/feedback/` emits these names.
