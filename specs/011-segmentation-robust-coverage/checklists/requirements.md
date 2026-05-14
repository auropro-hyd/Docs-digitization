# Requirements Checklist: Robust Segmentation Coverage and Consistency

**Purpose**: PR-review gate for Spec 011. Every item below maps to a functional requirement or success criterion; a green PR is one where every box is ticked or has a documented exception in the PR description.
**Created**: 2026-05-14
**Feature**: [spec.md](../spec.md) · [plan.md](../plan.md) · [tasks.md](../tasks.md)

## US1 — Page-header boundary respect (P1)

- [ ] CHK001 Implements `parse_page_headers()` against the first 200 chars of each page's markdown (FR-001).
- [ ] CHK002 Parser tolerates `Page X of Y`, `Page X/Y`, `pg X of Y`, `Pege X of Y` (typo); confidence reflects which variant fired.
- [ ] CHK003 `group_boundary_units()` produces one unit per contiguous run with constant `Y` and `X` starting at 1 (FR-002).
- [ ] CHK004 `merge_split_by_boundary()` merges multiple LLM sections inside one unit into one (FR-003).
- [ ] CHK005 `merge_split_by_boundary()` splits one LLM section spanning a unit transition into two (FR-004).
- [ ] CHK006 Section_type/document_type on a merged section come from the constituent that covers the most pages; tie-break lowest start_page.
- [ ] CHK007 Telemetry events `segmentation.header_boundary_merged` and `..._split` fire with from-ranges and to-ranges in payload (FR-005).
- [ ] CHK008 Manual smoke against the user's reference doc (2538105062.pdf): raw-material sub-forms collapse to one section per detected boundary unit (SC-002).

## US2 — Truncation + structural minimums (P1)

- [ ] CHK009 Implements `detect_truncation()` against `_TRUNCATION_COVERAGE_THRESHOLD = 0.97` (FR-006).
- [ ] CHK010 Truncation retry issues one additional LLM call covering the uncovered tail; results merge disjointly into the original output (FR-007).
- [ ] CHK011 Retry chain hard-caps at 2 attempts; remaining range becomes `section_type='unknown'` with a `retry_exhausted` event (FR-008).
- [ ] CHK012 Implements `validate_structural_minimums()` reading `required: true` from `document_profiles.yaml` (FR-009).
- [ ] CHK013 Missing-required-section issues are emitted as `validation_issues`, not as hard errors — the run completes.
- [ ] CHK014 Synthetic 200-page packet covered end-to-end after a stubbed-LLM truncation at page 100 (SC-003, integration test).
- [ ] CHK015 Fixture `batch_record` segmentation missing `cover_page` emits the `missing_required_section` issue (SC-004).

## US3 — Cross-evidence validators (P2)

- [ ] CHK016 `validate_kv_coverage()` emits `no_kv_evidence` for sections with `end_page - start_page >= 2` AND zero matching KV pairs (FR-010).
- [ ] CHK017 `validate_kv_coverage()` does NOT fire on 1- or 2-page sections (legitimately small).
- [ ] CHK018 `validate_type_consistency()` skips sections with `section_type='unknown'` or empty fields (FR-011).
- [ ] CHK019 `validate_type_consistency()` emits `type_mismatch` when section_type is not in document_type's profile.
- [ ] CHK020 Both validators are pure — no LLM call, no I/O.

## US4 — HITL-edit preservation (P2)

- [ ] CHK021 `PUT /api/compliance/{doc_id}/segmentation` diffs the incoming body and appends per-field overrides to `segmentation.overrides.json` (FR-012).
- [ ] CHK022 `X-Actor-Id` header is recorded on every override; defaults to `"unknown"` when absent.
- [ ] CHK023 Overrides are append-only on disk; `apply_overrides()` resolves last-record-wins per `(section_id, field)`.
- [ ] CHK024 `POST /api/compliance/{doc_id}/segment` applies overrides after the LLM call and before the geometric post-processes (FR-013).
- [ ] CHK025 An override whose target `section_id` is absent from the LLM output emits `override_orphaned` and is dropped (FR-013 edge case).
- [ ] CHK026 Atomic write via tmp+rename used for `segmentation.overrides.json` so a crash mid-write doesn't leave a corrupt sidecar.
- [ ] CHK027 Integration test: PUT edit → POST re-segment → GET reflects the edit (SC-005).
- [ ] CHK028 No overrides file → behaviour identical to today (no regression).

## FR-014 — Validation surface (cross-cutting)

- [ ] CHK029 `DocumentSegmentation.validation_issues: list[dict]` defaulted to `[]`; serialises in `GET /segmentation`.
- [ ] CHK030 Each issue dict has `kind` / `message` / `section_ids` / `page_range` (page_range may be null).
- [ ] CHK031 Issues persist with the segmentation (re-reads return the same array).
- [ ] CHK032 Empty array on a clean segmentation (no false positives).

## Quality gates

- [ ] CHK033 New unit tests are listed under `tasks.md` and have FAILed before implementation began (TDD discipline).
- [ ] CHK034 All new tests pass on the merge candidate.
- [ ] CHK035 Existing tests in `test_segmentation_robust_coverage.py` and `test_segmentation_postprocess_cleanup.py` (PR #69) still pass.
- [ ] CHK036 Manual smoke covers all six success criteria from [spec.md](../spec.md#measurable-outcomes).
- [ ] CHK037 PR description references this spec dir and the specific FRs / SCs delivered.
- [ ] CHK038 No new compliance-pipeline rules or LLM-call shapes invented outside the structured-output adapter (Constitution IX).
- [ ] CHK039 No client-specific values hardcoded in Python (Constitution VI); the coverage threshold lives as a module constant.

## Notes

- Tick boxes as the PR matures: `- [x]`.
- Anything left unticked at merge time needs a justification in the PR description ("intentionally deferred — follow-up in #N").
- For US-level smoke tests, prefer a real document (2538105062.pdf) over synthetic fixtures — synthetic ones often miss the exact OCR-layout quirks the spec is designed to handle.
