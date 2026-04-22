# REST API Contract: Audit Report & Finding-Level HITL

**Feature**: 004 | **Version**: v1 | Base path: `/api/v1`

All endpoints require auth; every write is audited with server-assigned timestamp +
actor id.

## 1. `GET /bmr/runs/{run_id}/report`

Return the consolidated grouped report.

**Query params**: `view=grouped|flat` (default `grouped`), `severity=…` (filter).

**Response 200**:
```json
{
  "run_id": "run_...",
  "view": "grouped",
  "export_gate": { "status": "blocked_by_pending_findings", "pending_blocking_count": 2 },
  "sections": [
    {
      "id": "step-03",
      "group_kind": "bpcr_step",
      "group_ref": { "step_number": 3 },
      "severity_counts": { "critical": 0, "major": 1, "minor": 2, "info": 0 },
      "all_actioned": false,
      "sub_sections": [
        { "kind": "alcoa",    "finding_ids": ["fnd_..."] },
        { "kind": "gmp",      "finding_ids": [] },
        { "kind": "checklist","finding_ids": ["fnd_syn_..."] }
      ]
    }
  ]
}
```

## 2. `GET /bmr/runs/{run_id}/findings/{finding_id}`

Full finding detail + resolved context (Spec 003) + evidence links.

**Response 200**:
```json
{
  "finding_id": "fnd_...",
  "rule_id": "alcoa.accurate.bpcr-raw-material-weight-match",
  "rule_version": "1.0.0",
  "severity": "major",
  "alcoa_tags": ["Accurate"],
  "source": "cross_doc_rule_eval.v1",
  "scope": { "kind": "bpcr_step", "step_number": 3 },
  "observed_values": { "source": 12.7, "target": 12.5 },
  "tolerance_applied": { "kind": "absolute", "value": 0.1, "unit": "kg" },
  "resolved_context": { ... },
  "evidence": [
    { "document_ref_id": "drf_bpcr_...", "page_number": 3, "region": {"x":..,"y":..,"w":..,"h":..} },
    { "document_ref_id": "drf_raw_...", "page_number": 1, "region": {"x":..,"y":..,"w":..,"h":..} }
  ],
  "synthesised_from": [],                            /* non-empty for checklist-synthesised */
  "current_resolution": null
}
```

## 3. `POST /bmr/runs/{run_id}/findings/{finding_id}/resolutions`

Create a `StructuredResolution` (CONFIRM or DISMISS).

**Request (CONFIRM)**:
```json
{ "action": "CONFIRM", "note": "verified against paper original" }
```

**Request (DISMISS, value-dependent reason)**:
```json
{
  "action": "DISMISS",
  "reason_type": "OCR_MISREAD",
  "observed_value_on_document": "12.7 kg",
  "reason_comment": "the 7 looks like a 5 on low-res scan"
}
```

**Request (DISMISS, other reason)**:
```json
{ "action": "DISMISS", "reason_type": "DUPLICATE_FINDING", "duplicate_of_finding_id": "fnd_..." }
```

**Response 201**: resolution object + newly-created `FeedbackSample.id` for traceability.

**Response 422**: validation failed (missing `reason_type`, free-text only, missing
`observed_value_on_document` for value-dependent type).

## 4. `POST /bmr/runs/{run_id}/findings/{finding_id}/corrections`

Create a `CorrectionWorkflow` and return a re-run preview.

**Request**:
```json
{
  "document_ref_id": "drf_raw_...",
  "field": "weight_kg",
  "to": 12.7,
  "reason_type": "ocr_misread",
  "reason_comment": "verified against paper batch record"
}
```

**Response 200** (preview phase):
```json
{
  "correction_id": "cwf_...",
  "status": "preview_computed",
  "rerun_plan": {
    "rule_ids": ["alcoa.accurate.bpcr-raw-material-weight-match", "..."],
    "estimated_findings_affected": 3,
    "estimated_duration_ms": 2800
  }
}
```

## 5. `POST /bmr/runs/{run_id}/corrections/{correction_id}/confirm`

Execute the re-run.

**Response 200**: `{status: "rerun_complete", invalidated: [...], added: [...]}`.

**WebSocket**: `rerun.planned` → `rerun.in_progress` → `rerun.completed` events streamed
(event contract §3).

## 6. `GET /bmr/runs/{run_id}/export-gate`

Summarise whether export is permitted.

**Response 200**:
```json
{
  "status": "blocked_by_pending_findings",
  "pending": [
    { "finding_id": "fnd_...", "severity": "major", "reason": "unactioned" },
    { "finding_id": "fnd_...", "severity": "critical", "reason": "superseded_needs_re_action" }
  ]
}
```

## 7. `POST /bmr/runs/{run_id}/reports:export`

Produce a new `AuditReportRevision`.

**Response 200**:
```json
{
  "revision_id": "rev_...",
  "revision_number": 2,
  "predecessor_id": "rev_...",
  "pdf_url": "/api/v1/reports/revisions/rev_.../pdf",
  "bundle_url": "/api/v1/reports/revisions/rev_.../bundle"
}
```

**Response 409** (gate blocked): `{ code: "export_blocked", details: { pending: [...] } }`.

## 8. `GET /reports/revisions/{revision_id}/pdf` and `/bundle`

Download immutable blobs by content-addressed URL. `Content-Type: application/pdf` or
`application/json`.

## 9. `GET /bmr/runs/{run_id}/audit-trail`

Ordered log of resolutions, corrections, re-runs, exports for this run.

## 10. `GET /feedback/samples`

Query the feedback corpus (backing Spec 005 rule-authoring skill).

**Query params**: `rule_id`, `rule_version`, `reason_type`, `since`.

**Response 200**:
```json
{
  "items": [
    {
      "sample_id": "fbs_...",
      "rule_id": "alcoa.accurate.bpcr-raw-material-weight-match",
      "rule_version": "1.0.0",
      "action": "DISMISS",
      "reason_type": "ACCEPTABLE_VARIANCE",
      "input_context_digest": "sha256:...",
      "finding_snapshot": { ... },
      "created_at": "..."
    }
  ],
  "next_page_token": "..."
}
```

## 11. Forbidden

- No endpoint in this spec mutates pipeline stage state beyond `REPORT_AND_RESOLUTION`.
- No mid-pipeline resolution endpoint exists; legibility mid-pipeline HITL is owned by
  Spec 001 and is restricted to `reupload`/`proceed`.
- No "overall compliance score" field exists in any response. CI checks the exporter
  bundle schema for forbidden keys.
