# REST API Contract: Document Package Ingestion & Classification

**Feature**: 002 | **Version**: v1 | Base path: `/api/v1`

All endpoints require auth. All timestamps are ISO-8601 UTC. All error bodies follow
`{ code: string, message: string, details?: object }`.

## 1. `POST /packages`

Upload a document package (multipart) or reference a pre-staged bundle.

**Request** (multipart/form-data):
- `manifest_name` (required, string) — e.g. `pilot-manifest-v1`
- `files[]` (required, one-or-more) — PDF files, or a single concatenated PDF
- `hint_roles[]` (optional, JSON) — `[{filename, role}]` reviewer-supplied hints

**Response 201**:
```json
{
  "package_id": "01H...",
  "status": "classifying",
  "document_count": 10,
  "expected_manifest": "pilot-manifest-v1"
}
```

**Response 422** (malformed inputs):
```json
{
  "code": "malformed_input",
  "message": "One or more files rejected",
  "details": { "rejected": [{"filename": "x.docx", "reason": "not_a_pdf"}] }
}
```

No partial package is persisted on 422.

## 2. `GET /packages/{package_id}`

Fetch package state + current classification snapshot.

**Response 200**:
```json
{
  "package_id": "01H...",
  "status": "classification_ready",
  "manifest": { "name": "pilot-manifest-v1", "version": "1.2.0" },
  "documents": [
    {
      "document_ref_id": "01H...",
      "original_filename": "BPCR-Batch-42.pdf",
      "page_range": null,
      "is_virtual": false,
      "current_classification": {
        "role": "BPCR",
        "confidence": 0.93,
        "decision_source": "header_heuristic",
        "candidates": [{"role":"BPCR","score":0.93},{"role":"BMR","score":0.05}]
      },
      "canonical_bpcr": true
    }
  ],
  "manifest_verification": {
    "status": "manifest_verified",
    "per_role": [{"role":"BPCR","observed":1,"min":1,"max":1}]
  }
}
```

## 3. `POST /packages/{package_id}/classifications/{document_ref_id}/override`

Reviewer correction of a document's role.

**Request**:
```json
{
  "new_role": "EQUIPMENT_LOG",
  "reason_type": "wrong_role",
  "reason_comment": "header says Equipment Usage Log"
}
```

**Response 200**: new `ClassificationResult` + updated `manifest_verification` (must
re-verify within SC-003 ≤ 3 s).

**Response 409** (would create duplicate canonical BPCR): `code: "duplicate_canonical_bpcr"`.

## 4. `POST /packages/{package_id}/canonical-bpcr`

Designate one BPCR as canonical.

**Request**: `{ "document_ref_id": "01H..." }`.

**Response 200**: package `status` advances; designation captured in audit trail.

**Response 409**: target doc is not currently classified as `BPCR`.

## 5. `POST /packages/{package_id}/boundaries/override`

Reviewer correction of a concatenated-PDF split.

**Request**:
```json
{
  "source_physical_file_id": "01H...",
  "resulting_ranges": [
    { "start": 1, "end": 40, "role_hint": "BMR" },
    { "start": 41, "end": 120, "role_hint": "BPCR" }
  ],
  "reason_comment": "headers misread on p.41"
}
```

**Response 200**: new `DocumentRef`s materialised as virtuals; previous virtuals'
`supersedes_id` chain extended.

## 6. `GET /config/manifests` and `GET /config/summary-templates`

List currently loaded YAML-driven manifests and summary templates.

**Response 200**: `{ items: [{ name, version, loaded_at, ... }] }`.

Operators may POST to `/config/manifests:reload` (admin-only) to re-read YAML without a full
service restart; this is a soft-reload for dev/QA only (production uses rolling restart).

## 7. `GET /packages/{package_id}/summaries?document_ref_id=…&scope=…`

Return generated summaries for a document, optionally filtered by scope.

**Response 200**:
```json
{
  "items": [
    {
      "summary_id": "01H...",
      "document_ref_id": "01H...",
      "page_number": 3,
      "template_id": "01H...",
      "content": { "step_id": "STEP-014", "operator_init": "JR", "timestamp": "…", "note": "" },
      "generated_at": "…",
      "generated_by": "page_summary.v1@2.0.1"
    }
  ]
}
```

## 8. `GET /packages/{package_id}/audit-trail`

Return the append-only log of classification and boundary overrides plus canonical-BPCR
designation events for this package.

## 9. Forbidden / out-of-scope

- No endpoint in this contract emits or mutates `Finding` rows. Findings are owned by
  Spec 001's pipeline contract.
- No endpoint in this contract accepts a `resolution` or `correction` action; those belong
  to Spec 004.
