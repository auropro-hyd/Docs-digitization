# Quickstart: Document Package Ingestion & Classification

**Feature**: 002 | **Spec Version**: v2

End-to-end developer walkthrough. Assumes the backend + Postgres + frontend are running per
the repo root README.

## Prerequisites

- Postgres with migrations for packages / classifications / boundaries / summaries applied
  (`alembic upgrade head`).
- `config/bmr/pilot-manifest.yaml` and `config/bmr/pilot-summary-templates.yaml` present.
- Fixtures at `backend/tests/fixtures/bmr/pilot-package/`.

## 1. Load YAML config

```bash
cd backend
uv run python -m app.cli.load_config \
  --manifest config/bmr/pilot-manifest.yaml \
  --summary-templates config/bmr/pilot-summary-templates.yaml
```

Expect: `loaded manifest pilot-manifest-v1@1.2.0; templates: 7`.

## 2. Upload a well-labelled package (happy path)

```bash
curl -X POST http://localhost:8000/api/v1/packages \
  -F "manifest_name=pilot-manifest-v1" \
  -F "files[]=@tests/fixtures/bmr/pilot-package/bmr.pdf" \
  -F "files[]=@tests/fixtures/bmr/pilot-package/bpcr.pdf" \
  -F "files[]=@tests/fixtures/bmr/pilot-package/dispensing-log.pdf"
```

Poll `GET /api/v1/packages/{id}` until `status=manifest_verified` (expect ≤ 60 s per
SC-002). Every document should have `decision_source=filename_heuristic` or
`header_heuristic`.

## 3. Upload a concatenated PDF (boundary detection)

```bash
curl -X POST http://localhost:8000/api/v1/packages \
  -F "manifest_name=pilot-manifest-v1" \
  -F "files[]=@tests/fixtures/bmr/concatenated-bundle.pdf"
```

Expect the response package to expose 3 virtual `DocumentRef`s after classification, each
with `is_virtual=true` and a populated `page_range`. Inspect the audit trail:

```bash
curl http://localhost:8000/api/v1/packages/{id}/audit-trail | jq '.items[] | select(.type=="boundary_override")'
```

`detected_by` should be `page_header` for the bundle's pages 1–40 / 41–120 / 121–210.

## 4. Upload an ambiguous document (reviewer override path)

```bash
curl -X POST http://localhost:8000/api/v1/packages \
  -F "manifest_name=pilot-manifest-v1" \
  -F "files[]=@tests/fixtures/bmr/ambiguous/scan-lowres.pdf"
```

Expect `status=needs_review` and the ambiguous document with
`decision_source=vlm_tiebreak` or `candidates[0].score < 0.85`.

Override the role:

```bash
curl -X POST http://localhost:8000/api/v1/packages/{pkg_id}/classifications/{doc_ref_id}/override \
  -H "Content-Type: application/json" \
  -d '{"new_role":"EQUIPMENT_LOG","reason_type":"wrong_role","reason_comment":"header says Equipment Usage Log"}'
```

Expect manifest re-verification to complete in ≤ 3 s (SC-003) and a new
`ClassificationResult` head whose `decision_source=reviewer_override`.

## 5. Designate canonical BPCR when duplicates exist

Upload a package with two BPCR files. Expect `needs_review` + a
`duplicate_canonical_bpcr` indicator. Designate:

```bash
curl -X POST http://localhost:8000/api/v1/packages/{pkg_id}/canonical-bpcr \
  -H "Content-Type: application/json" \
  -d '{"document_ref_id":"01H..."}'
```

Expect `status` to advance and the non-canonical BPCR to remain in the package with
`canonical=false`.

## 6. Reject malformed input

```bash
curl -X POST http://localhost:8000/api/v1/packages \
  -F "manifest_name=pilot-manifest-v1" \
  -F "files[]=@tests/fixtures/bmr/malformed/non-pdf.docx" \
  -F "files[]=@tests/fixtures/bmr/malformed/corrupt.pdf" \
  -F "files[]=@tests/fixtures/bmr/malformed/password-protected.pdf"
```

Expect HTTP 422 with each file enumerated. Confirm via
`SELECT COUNT(*) FROM document_package WHERE uploaded_by='...'` that NO package was created
(FR-008).

## 7. Inspect summaries

Once `status=manifest_verified`, fetch summaries:

```bash
curl "http://localhost:8000/api/v1/packages/{pkg_id}/summaries?scope=page&document_ref_id={bpcr_doc_id}"
curl "http://localhost:8000/api/v1/packages/{pkg_id}/summaries?scope=document&document_ref_id={sop_doc_id}"
```

Expect BPCR summaries = 1 per page; SOP summary = 1 per document.

## 8. Hand-off to Spec 001 pipeline

Starting a BMR audit run now uses an already-prepared package:

```bash
curl -X POST http://localhost:8000/api/v1/bmr/runs \
  -H "Content-Type: application/json" \
  -d '{"package_id":"01H..."}'
```

(Spec 001's pipeline endpoint — included here for completeness.)

## 9. Regression check — single-file upload unchanged

```bash
cd backend && uv run pytest tests/regression/test_single_file_upload_unchanged.py -v
```

All must pass (Constitution VII gate).

## 10. Constitution spot-check

- ALCOA+ audit trail: `SELECT COUNT(*) FROM classification_override WHERE actor_id IS NULL`
  must return 0.
- Configurable framework: no role string literal appears in
  `app/classification/*.py` except the `{BMR, BPCR, OTHER}` sentinel set.
- Capability atomicity: `app/capabilities/{boundary_detect,page_summary,doc_summary}.v1.py`
  each implement `Capability` ABC and pass `tests/capabilities/`.
