# Quickstart: Robust Segmentation Coverage

**Feature**: 011-segmentation-robust-coverage
**Audience**: An engineer who's pulled the branch and wants to validate the new behaviour end-to-end.

## Prereqs

- Backend venv set up (`make backend-install` or equivalent).
- A processed document in `data/documents/` with `result.json` and `compliance_result.json` already on disk. The user's 2026-05-13 reference doc is `cd04526b-3ff1-4332-bf29-d4b2fcb0658e`.

## 1. Run the full backend suite (smoke baseline)

```bash
cd backend
DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib:$DYLD_FALLBACK_LIBRARY_PATH" \
  .venv/bin/python -m pytest -q
```

Expected: all tests pass. The new test files added under this spec should be included; if any pre-existing tests fail, you've hit a regression — investigate before continuing.

## 2. US1 smoke — page-header boundary respect

```bash
# Re-trigger segmentation on the user's reference doc.
curl -X POST http://localhost:8100/api/compliance/cd04526b-3ff1-4332-bf29-d4b2fcb0658e/segment

# Wait ~30 seconds, then read back the segmentation.
curl -s http://localhost:8100/api/compliance/cd04526b-3ff1-4332-bf29-d4b2fcb0658e/segmentation \
  | jq '.sections | map({pages: [.start_page, .end_page], type: .section_type, name: .name})'
```

**Verify**:

- The raw-material sub-forms (pages 30-52 in the reference doc) collapse into one section per detected `Page X of Y` boundary unit, not one section per LLM emission.
- No section's `[start_page, end_page]` overlaps another's.
- `segmentation.header_boundary_merged` events appear in the telemetry sink if you have one bound.

## 3. US2 smoke — truncation + structural minimums

```bash
# Pin against a synthetic 200-page packet via the unit test (no real LLM call):
.venv/bin/python -m pytest tests/compliance/test_segmentation_truncation.py -v

# Validate structural minimums against the reference doc — should NOT emit
# missing_required_section for batch_record (the cover_page is present).
.venv/bin/python -c "
from app.compliance.segmentation import validate_segmentation
from app.compliance.models import DocumentSegmentation
import json
seg = DocumentSegmentation.model_validate(
    json.load(open('data/documents/cd04526b-3ff1-4332-bf29-d4b2fcb0658e/segmentation.json'))
)
issues = validate_segmentation(seg, total_pages=115)
print('Issue kinds:', sorted({i.kind for i in issues}))
"
```

**Verify**:

- The truncation unit test produces a final segmentation that covers all 200 pages.
- The reference doc surfaces zero `missing_required_section` issues.
- Constructing a fake `batch_record` segmentation with no `cover_page` SHOULD emit the missing-required-section issue — exercise that via the unit test.

## 4. US3 smoke — cross-evidence validators

```bash
.venv/bin/python -m pytest tests/compliance/test_segmentation_validators.py -v
```

**Verify**:

- `no_kv_evidence` fires only on sections with ≥3 pages and zero KV pairs in range.
- `type_mismatch` fires on the contradictory `manufacturing_operations` + `ipc_report` fixture.
- `type_mismatch` does NOT fire on `section_type='unknown'`.

## 5. US4 smoke — HITL-edit preservation

```bash
# Save an operator override that extends a section's end_page by 2.
curl -X PUT http://localhost:8100/api/compliance/cd04526b-3ff1-4332-bf29-d4b2fcb0658e/segmentation \
  -H "Content-Type: application/json" \
  -H "X-Actor-Id: smoke-test@auropro.com" \
  -d '{"sections": [..., {"section_id": "raw_material_request_and_issue", "end_page": 47, ...}], ...}'

# Confirm the override was persisted.
cat data/documents/cd04526b-3ff1-4332-bf29-d4b2fcb0658e/segmentation.overrides.json

# Trigger re-segmentation.
curl -X POST http://localhost:8100/api/compliance/cd04526b-3ff1-4332-bf29-d4b2fcb0658e/segment

# Read back; verify the override is reflected.
curl -s http://localhost:8100/api/compliance/cd04526b-3ff1-4332-bf29-d4b2fcb0658e/segmentation \
  | jq '.sections[] | select(.section_id == "raw_material_request_and_issue") | .end_page'
```

**Verify**:

- After the re-run, the section's `end_page` is **47** (the operator's override), not whatever the fresh LLM returned.
- The override file has a record with `actor: "smoke-test@auropro.com"` and the current timestamp.

## 6. Cross-cutting — validation issues in HITL response (FR-014)

```bash
curl -s http://localhost:8100/api/compliance/cd04526b-3ff1-4332-bf29-d4b2fcb0658e/segmentation \
  | jq '.validation_issues'
```

**Verify**:

- The array is present in every response (empty when clean).
- After re-segmentation, any LLM artefacts surface here (overlap, missing-required-section, type-mismatch, etc.) with `kind`, `message`, `section_ids`, `page_range`.

## 7. Reset state for re-runs

```bash
# Wipe just the overrides (reverts to LLM defaults next re-run).
rm data/documents/<doc_id>/segmentation.overrides.json

# Wipe the segmentation entirely (forces a fresh LLM call).
rm data/documents/<doc_id>/segmentation.json
```

## Done

If all six steps pass on the user's reference doc, the spec's success criteria SC-001 through SC-006 are met.
