# API Contract: Robust Segmentation Coverage (Spec 011)

## Surface area

No new endpoints. Two existing endpoints change shape (additive); one operational behaviour changes.

| Method | Path | Status | Change |
|---|---|---|---|
| `GET` | `/api/compliance/{doc_id}/segmentation` | **additive content; same URL** | adds `validation_issues[]` to the response (FR-014) |
| `PUT` | `/api/compliance/{doc_id}/segmentation` | **additive side-effect; same URL** | also writes per-field operator overrides to a sidecar (FR-012) |
| `POST` | `/api/compliance/{doc_id}/segment` | **additive behaviour; same URL** | applies stored overrides after the LLM call before persisting (FR-013) |

## `GET /api/compliance/{doc_id}/segmentation`

**Existing endpoint, content additive.**

### Query parameters

None.

### Response

- `200 OK` — JSON body matches today's `DocumentSegmentation` shape with one new field appended:

```json
{
  "sections": [
    {
      "section_id": "raw_material_request_and_issue",
      "name": "Raw Material Request & Issue",
      "section_type": "material_request",
      "document_type": "raw_material_request",
      "start_page": 30,
      "end_page": 32,
      "description": "..."
    }
  ],
  "document_type": "batch_record",
  "confidence": 0.91,
  "validation_issues": [
    {
      "kind": "header_boundary_merged",
      "message": "Sections 'rm_req_pt1' (30-31) and 'rm_iss_pt2' (32) merged: pages share Page X of 3 header",
      "section_ids": ["rm_req_pt1", "rm_iss_pt2"],
      "page_range": [30, 32]
    },
    {
      "kind": "missing_required_section",
      "message": "batch_record profile requires 'cover_page' but no section emitted it",
      "section_ids": [],
      "page_range": null
    }
  ]
}
```

- `404 Not Found` — no `segmentation.json` for the doc_id.

### `validation_issues[]` schema

| Field | Type | Notes |
|---|---|---|
| `kind` | string | one of the kinds listed in [data-model.md](../data-model.md#extension-of-segmentationissuekind-existing-model) |
| `message` | string | human-readable description; safe to display in the editor UI verbatim |
| `section_ids` | `string[]` | may be empty when the issue is doc-wide (e.g. `missing_required_section`) |
| `page_range` | `[start, end]` or `null` | inclusive; `null` when the issue isn't page-scoped |

### Backward compatibility

Clients that ignore unknown fields see no behavioural change. The frontend editor will pick up `validation_issues` in a follow-up PR.

## `PUT /api/compliance/{doc_id}/segmentation`

**Existing endpoint, side-effect added.**

### Request

Body is the full `DocumentSegmentation` JSON (today's contract). No shape change.

### Required headers

| Header | Required | Notes |
|---|---|---|
| `Content-Type: application/json` | yes | existing |
| `X-Actor-Id` | recommended | operator identity recorded on each override; defaults to `"unknown"` when absent |

### Side-effects (new)

In addition to overwriting `segmentation.json` (today's behaviour), the server:

1. Loads the current `segmentation.json`.
2. Diffs incoming sections against current per field (`name`, `section_type`, `document_type`, `start_page`, `end_page`).
3. For each changed `(section_id, field)`, appends a `SegmentationOverride` record to `data/documents/{doc_id}/segmentation.overrides.json`:

```json
[
  {
    "section_id": "raw_material_request_and_issue",
    "field": "end_page",
    "value": 47,
    "recorded_at": "2026-05-14T12:34:00Z",
    "actor": "anmol@auropro.com"
  }
]
```

The file is append-only in spirit (we keep history for audit). On `POST /segment` re-runs, the last record per `(section_id, field)` wins.

### Response

- `200 OK` — body is `{"status": "updated", "overrides_recorded": <count>}`. `overrides_recorded` is 0 when the PUT made no field-level changes.
- `404 Not Found` — doc_id directory doesn't exist.

## `POST /api/compliance/{doc_id}/segment`

**Existing endpoint, behaviour additive.**

### Request

No body. Triggers a fresh segmentation.

### Behaviour (new step in italics)

1. Read `result.json` extractions + key_value_pairs.
2. Call the LLM via `DocumentSegmenter.segment()`.
3. `clamp_page_ranges` (PR #69).
4. ***Load `segmentation.overrides.json` if present; apply per `(section_id, field)`. Emit `override_orphaned` for missing targets.*** (FR-013, new)
5. `resolve_overlaps` (PR #69).
6. ***Detect truncation; retry tail LLM call up to 2 attempts.*** (FR-006 through FR-008, new)
7. `fill_gaps_with_unknown` (PR #45).
8. ***Merge / split by header boundary units.*** (FR-003 / FR-004, new)
9. `normalize_section_types_to_canonical` (PR #69).
10. `stamp_document_types` (PR #69).
11. ***Run new validators: `validate_kv_coverage`, `validate_type_consistency`, `validate_structural_minimums`.*** (FR-009 / FR-010 / FR-011, new)
12. Persist segmentation + populate `validation_issues`.

### Response

- `200 OK` — `{"status": "started"}` (the heavy work is a background task; the client polls `GET /segmentation`).
- `400 Bad Request` — document not processed yet (no `result.json`).
- `404 Not Found` — doc_id not found.

## Telemetry events introduced

All routed through `app.observability.run_telemetry.record_event(...)` at `level="warning"`.

| Event | Source FR | Fields |
|---|---|---|
| `segmentation.header_boundary_merged` | FR-005 | `from_sections`, `to_range` |
| `segmentation.header_boundary_split` | FR-005 | `section_id`, `at_page`, `from_range`, `to_ranges` |
| `segmentation.header_low_confidence` | FR-001 edge | `page_num`, `raw`, `confidence` |
| `segmentation.boundary_conflict` | edge case | `pages`, `y_values` |
| `segmentation.output_truncated` | FR-006 | `total_pages`, `covered_pages`, `coverage_ratio`, `finish_reason` |
| `segmentation.retry_exhausted` | FR-008 | `attempts`, `uncovered_range` |
| `segmentation.missing_required_section` | FR-009 | `document_type`, `section_type` |
| `segmentation.no_kv_evidence` | FR-010 | `section_id`, `page_range` |
| `segmentation.type_mismatch` | FR-011 | `section_id`, `section_type`, `document_type` |
| `segmentation.override_orphaned` | FR-013 | `section_id`, `field`, `value` |

Plus the existing events from PR #69 (`segmentation.range_clipped`, `segmentation.range_dropped`, `segmentation.overlap_clamped`, `segmentation.overlap_dropped`, `segmentation.section_type_normalised`, `segmentation.section_type_collapsed_to_unknown`, `segmentation.section_type_matches_doc_type`).

## What does NOT change

- The compliance pipeline (`compliance_result.json` schema).
- The export pipeline (Spec 008).
- The mitigation synth endpoint.
- The frontend segmentation editor — picks up the new `validation_issues` field opportunistically; doesn't break if it ignores it.
- LLM provider adapters — they don't need to expose `finish_reason` for this spec to work (it's a tie-breaker, not a requirement).
