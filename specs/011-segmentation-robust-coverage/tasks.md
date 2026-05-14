---
description: "Task list for Robust Segmentation Coverage and Consistency (011)"
---

# Tasks: Robust Segmentation Coverage and Consistency

**Input**: Design documents from `/specs/011-segmentation-robust-coverage/`
**Prerequisites**: `plan.md`, `spec.md` (mandatory), `research.md`, `data-model.md`, `contracts/api-contract.md`

**Tests**: Included throughout — every new pure function and every endpoint shape change is pinned by tests written before implementation.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4, US-cross for FR-014)

## Path Conventions

- Backend: `backend/app/compliance/`, `backend/tests/compliance/`, `backend/tests/integration/`
- All paths from repo root.

---

## Phase 1: Setup (Shared Infrastructure)

- [ ] **T001** [P] Create `backend/app/compliance/segmentation_headers.py` with module docstring (page-header parser + boundary-unit grouper). Empty stubs for `PageHeader`, `BoundaryUnit`, `parse_page_headers`, `group_boundary_units`.
- [ ] **T002** [P] Create `backend/app/compliance/segmentation_overrides.py` with module docstring (sidecar load/save + apply). Empty stubs for `SegmentationOverride`, `load_overrides`, `save_override`, `apply_overrides`.
- [ ] **T003** [P] Extend `DocumentSegmentation` in `backend/app/compliance/models.py` with the additive field `validation_issues: list[dict] = Field(default_factory=list)`. Update the model's docstring referencing FR-014.
- [ ] **T004** Verify the full test suite still passes against the additive model change (regression baseline before any new work).

---

## Phase 2: Foundational — US1 (Priority: P1) 🎯 MVP

**Goal**: Page-header boundary respect. Closes the user's "splits a page-1-of-3 doc into two" complaint.

**Independent Test**: Given a 3-page raw-material form with `Page X of 3` headers and a deliberately wrong LLM output, the post-process emits a single merged section.

### Tests for US1 (write FIRST — they MUST fail before implementation)

- [ ] **T005** [P] [US1] Write tests `backend/tests/compliance/test_segmentation_headers.py` — `parse_page_headers` happy path on `Page 1 of 3` / `Page 2 of 3` / `Page 3 of 3`, tolerated-typo variants (`Pege`, `pg`, `Page X/Y`), no-match path returns empty list, headers past the first 200 chars are ignored.
- [ ] **T006** [P] [US1] Write tests for `group_boundary_units` — three consecutive pages with `1/2/3 of 3` form one unit, mixed-Y values split into two units, gaps in `X` sequence (1/3/...) emit `header_low_confidence`, single-page `1 of 1` is its own unit.
- [ ] **T007** [US1] Write tests `backend/tests/compliance/test_segmentation_boundary_merge.py` — LLM splits a 3-page boundary unit, merger collapses to one (FR-003); LLM glues two units, merger splits at the Y-change page (FR-004); winning section_type chosen by page-coverage majority with lowest-start tie-break.

### Implementation for US1

- [ ] **T008** [US1] Implement `parse_page_headers(extractions: list[dict]) -> list[PageHeader]` in `segmentation_headers.py`. Operate on the first 200 chars of each page's markdown; use the tolerant regex from FR-001; confidence 1.0 for `Page X of Y`, 0.7 for typo variants.
- [ ] **T009** [US1] Implement `group_boundary_units(headers: list[PageHeader]) -> list[BoundaryUnit]` in `segmentation_headers.py`. Group runs where Y is constant and X is monotonically non-decreasing starting at 1.
- [ ] **T010** [US1] Implement `merge_split_by_boundary(seg: DocumentSegmentation, units: list[BoundaryUnit]) -> DocumentSegmentation` in `segmentation.py`. Walks the LLM segmentation; merges sections inside one unit, splits sections that straddle a unit transition. Emits `header_boundary_merged` / `header_boundary_split` events.
- [ ] **T011** [US1] Wire `merge_split_by_boundary` into `DocumentSegmenter.segment()` after `fill_gaps_with_unknown`, before `normalize_section_types_to_canonical`. Pass through the headers / units list from `parse_page_headers` + `group_boundary_units` computed once per call.

**Checkpoint**: US1 ships end-to-end.

---

## Phase 3 — US2: Output-truncation detection + structural minimums (Priority: P1)

**Goal**: Catch silent LLM truncation + missing required sections. Closes the BPCR-collapsed-to-one-section regression.

**Independent Test**: With a 200-page packet and a stubbed LLM that truncates at 100, the segmenter detects, retries, and the final coverage is 1-200.

### Tests for US2

- [ ] **T012** [P] [US2] Write tests `backend/tests/compliance/test_segmentation_truncation.py` — coverage shortfall below 0.97 triggers retry (FR-006); retry merges disjointly with the original output (FR-007); two attempts then `retry_exhausted` (FR-008); shortfall above 0.97 does NOT retry.
- [ ] **T013** [P] [US2] Write tests `backend/tests/compliance/test_segmentation_structural_min.py` — `batch_record` missing `cover_page` emits `missing_required_section` (FR-009); a doc_type with no required sections emits nothing; `unknown` doc_type (post-clamp / drift) doesn't crash the check.

### Implementation for US2

- [ ] **T014** [US2] Implement `detect_truncation(seg: DocumentSegmentation, total_pages: int, finish_reason: str | None) -> int | None` in `segmentation.py`. Returns the page from which the retry should start, or None when coverage is acceptable. Threshold `_TRUNCATION_COVERAGE_THRESHOLD = 0.97`.
- [ ] **T015** [US2] Add `_segment_range()` helper in `DocumentSegmenter` that re-prompts the LLM for a sub-range of pages; returns a `DocumentSegmentation` whose page indices are re-keyed to the absolute packet positions.
- [ ] **T016** [US2] Wire truncation retry loop into `DocumentSegmenter.segment()`: after `clamp_page_ranges`, call `detect_truncation`; if non-None, run `_segment_range`, merge disjointly, repeat up to 2 attempts; fill remaining range with `unknown` after exhaustion.
- [ ] **T017** [US2] Implement `validate_structural_minimums(seg: DocumentSegmentation) -> list[SegmentationIssue]` in `segmentation.py`. Walks `seg.sections`; per emitted `document_type`, loads its profile's `required: true` sections from `document_profiles.yaml`; emits one `missing_required_section` issue per absence.
- [ ] **T018** [US2] Hook `validate_structural_minimums` into the existing `validate_segmentation` so the new issues flow through the same telemetry / response path.

**Checkpoint**: US2 ships end-to-end.

---

## Phase 4 — US3: Cross-evidence validators (Priority: P2)

**Goal**: Catch sections with no KV-pair evidence and contradictory section_type/document_type pairs.

**Independent Test**: A section with 6 pages and zero KV pairs in range emits `no_kv_evidence`; a section with `section_type='manufacturing_operations'` and `document_type='ipc_report'` emits `type_mismatch`.

### Tests for US3

- [ ] **T019** [P] [US3] Write tests `backend/tests/compliance/test_segmentation_validators.py` — `no_kv_evidence` fires only when section spans ≥3 pages AND KV count == 0; doesn't fire on 1- or 2-page sections (legitimately small) (FR-010).
- [ ] **T020** [P] [US3] Add tests for `type_mismatch` — manufacturing_operations + ipc_report fires; same section_type within its profile does not fire; `section_type='unknown'` does not fire (FR-011).

### Implementation for US3

- [ ] **T021** [US3] Implement `validate_kv_coverage(seg: DocumentSegmentation, kv_pairs: list[dict]) -> list[SegmentationIssue]` in `segmentation.py`. Iterate sections with `end_page - start_page >= 2`; count KV pairs with `page_num` in range; emit issue when zero.
- [ ] **T022** [US3] Implement `validate_type_consistency(seg: DocumentSegmentation) -> list[SegmentationIssue]` in `segmentation.py`. Skip sections with empty section_type / document_type / `section_type='unknown'`. For the rest, check membership in the profile's `expected_sections` + aliases; emit `type_mismatch` when missing.
- [ ] **T023** [US3] Thread `key_value_pairs` from `DocumentSegmenter.segment()` into `validate_segmentation`. Update the existing signature to accept it as an optional kwarg with default `None` (skip the validator when not supplied — keeps existing tests intact).

**Checkpoint**: US3 ships end-to-end.

---

## Phase 5 — US4: HITL-edit preservation (Priority: P2)

**Goal**: Operator edits survive any `POST /segment` re-run.

**Independent Test**: Save an override extending a section's `end_page`; trigger re-segmentation; the resulting `segmentation.json` carries the operator's value, not the fresh LLM value.

### Tests for US4

- [ ] **T024** [P] [US4] Write tests `backend/tests/compliance/test_segmentation_overrides.py` — `load_overrides` returns empty when no sidecar exists; `save_override` appends to the sidecar (preserving history); last record per `(section_id, field)` wins on `apply_overrides`.
- [ ] **T025** [P] [US4] Write tests for orphaned overrides — override target absent from the new LLM output emits `override_orphaned` and is dropped (FR-013).
- [ ] **T026** [US4] Write integration test `backend/tests/integration/test_segmentation_endpoint.py` — `PUT /api/compliance/{doc_id}/segmentation` writes to `segmentation.overrides.json`; subsequent `POST /api/compliance/{doc_id}/segment` (with stubbed LLM returning a different value) reflects the override in the response.

### Implementation for US4

- [ ] **T027** [US4] Implement `SegmentationOverride` Pydantic model in `segmentation_overrides.py` with fields per `data-model.md`.
- [ ] **T028** [US4] Implement `load_overrides(doc_dir: Path) -> list[SegmentationOverride]` and `save_override(doc_dir: Path, override: SegmentationOverride) -> None` (atomic write via tmp+rename).
- [ ] **T029** [US4] Implement `apply_overrides(seg: DocumentSegmentation, overrides: list[SegmentationOverride]) -> tuple[DocumentSegmentation, list[SegmentationIssue]]`. Returns the patched segmentation plus a list of `override_orphaned` issues for missing targets.
- [ ] **T030** [US4] Wire `apply_overrides` into `DocumentSegmenter.segment()` after `clamp_page_ranges` and before `resolve_overlaps` (so the operator's geometry is respected by the overlap resolver).
- [ ] **T031** [US4] Update `PUT /api/compliance/{doc_id}/segmentation` in `compliance.py` to diff the incoming body against the current `segmentation.json`, derive the per-field overrides, and append them via `save_override`. Actor from `X-Actor-Id` header (fall back to `"unknown"`).

**Checkpoint**: US4 ships end-to-end.

---

## Phase 6 — Cross-cutting: surface `validation_issues` in HITL response (FR-014)

- [ ] **T032** [US-cross] Populate `DocumentSegmentation.validation_issues` in `DocumentSegmenter.segment()` after all validators run; serialise via `SegmentationIssueDict`-shaped dicts.
- [ ] **T033** [US-cross] Update `GET /api/compliance/{doc_id}/segmentation` in `compliance.py` to return the field as part of the JSON body. (No code change if the model already serialises it — verify and add a regression test.)
- [ ] **T034** [P] [US-cross] Integration test: a doc that triggers two issues (e.g. `missing_required_section` + `type_mismatch`) returns both in the response array.

**Checkpoint**: All user stories independently functional + HITL surface ready for frontend pickup.

---

## Phase 7 — Polish

- [ ] **T035** Run the full backend suite and confirm 0 regressions in `tests/compliance/test_segmentation_robust_coverage.py` and `tests/compliance/test_segmentation_postprocess_cleanup.py` (PR #69 work).
- [ ] **T036** [P] Manual smoke on the user's real document (2538105062.pdf): re-trigger segmentation and verify (a) no overlap, (b) no `unsectioned`, (c) raw-material sub-forms collapse to boundary-unit count, (d) `validation_issues` array is populated for any LLM artefacts, (e) operator edits made via the editor persist across a re-run.
- [ ] **T037** [P] Update `.specify/feature.json` to point at this spec.
- [ ] **T038** Update `MEMORY.md` / save any non-obvious operator-facing knowledge (e.g. "operator edits persist in segmentation.overrides.json — wipe that file to reset to LLM defaults").

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: no deps; start immediately.
- **Phase 2 (US1)**: depends on Phase 1 (T001-T004).
- **Phase 3 (US2)**: depends on Phase 1; independent of US1 (can run in parallel by a second developer).
- **Phase 4 (US3)**: depends on Phase 1; independent of US1 and US2.
- **Phase 5 (US4)**: depends on Phase 1; independent of US1 / US2 / US3.
- **Phase 6 (FR-014)**: depends on at least one validator from US2/US3/US4 to have non-empty output to surface. Practically: after Phase 3 lands.
- **Phase 7 (Polish)**: depends on all desired user stories being complete.

### Within Each User Story

- Tests are written and must FAIL before implementation tasks start. Each [P] test task is independent.
- Modules before pipeline-wiring (e.g. T008 / T009 before T010).
- Pipeline-wiring before integration tests (T011 before manual smoke).

### Parallel Opportunities

- All Phase 1 setup tasks marked [P] can run in parallel (different files).
- All tests for a given user story marked [P] can be written in parallel.
- US1, US2, US3, US4 implementations can proceed in parallel by 4 developers after Phase 1 lands.

## Parallel Example: US1 tests

```bash
# Write all US1 tests together:
Task: "T005 parse_page_headers tests in tests/compliance/test_segmentation_headers.py"
Task: "T006 group_boundary_units tests in tests/compliance/test_segmentation_headers.py"
Task: "T007 merge_split_by_boundary tests in tests/compliance/test_segmentation_boundary_merge.py"
```

## Implementation Strategy

### MVP First (US1 alone)

1. Phase 1 (Setup) → Phase 2 (US1).
2. Validate against the user's reported "page-1-of-3 split" failure on a real doc.
3. Ship.

### Incremental Delivery

1. MVP (US1) → ship → measure.
2. Add US2 (truncation + structural minimums) — ship.
3. Add US3 (validators) — ship.
4. Add US4 (HITL preservation) — ship.

Each phase is independently shippable; each closes a distinct class of failure from the inversion analysis.

### Parallel Team Strategy

After Phase 1 lands (≤2 hours' work):

- Developer A: US1 (page-header boundary respect) — 4-6 hours
- Developer B: US2 (truncation + structural min) — 4-6 hours
- Developer C: US3 (cross-evidence validators) — 2-4 hours
- Developer D: US4 (HITL preservation) — 4-6 hours

All four merge into the same `validate_segmentation` / `DocumentSegmenter.segment()` surface — coordination cost is low because each developer owns a distinct module / function.

## Notes

- `[P]` tasks = different files, no dependencies.
- `[Story]` label maps each task to its user story for traceability.
- Tests written first must FAIL before implementation begins.
- Commit after each task or logical group; small focused commits make the squash-merge clean.
- Avoid: in-flight changes to `segmentation.py` from multiple developers without a clear lockstep — the post-process pipeline order matters and reordering is a foot-gun.
