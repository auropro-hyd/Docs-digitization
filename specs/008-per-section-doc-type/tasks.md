# Tasks: Per-Section Document Type Classification

**Input**: Design documents from `specs/008-per-section-doc-type/`  
**Branch**: `008-per-section-doc-type`  
**Plan**: [plan.md](plan.md) | **Spec**: [spec.md](spec.md)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story this task belongs to — [US1], [US2], [US3]
- Exact file paths included in all descriptions

---

## Phase 1: Foundational — Model Extension

**Purpose**: Add the `document_type` field to `DocumentSection`. Every subsequent task depends on this model change. Pydantic's default of `""` is what makes old `segmentation.json` caches backward-compatible (US3).

**⚠️ CRITICAL**: No user story implementation can begin until T001 is complete.

- [ ] T001 Add `document_type: str = ""` plain field to `DocumentSection` in `backend/app/compliance/models.py` (no validator; raw LLM output preserved in segmentation.json)

**Checkpoint**: `DocumentSection` accepts `document_type`; old JSON without the field deserializes cleanly with `document_type == ""`.

---

## Phase 2: User Story 1 — Correct Rule Filtering Per Sub-Document (Priority: P1) 🎯 MVP

**Goal**: Each section in `segmentation.json` carries a `document_type` from the 9 canonical keys, and the evaluator uses it per page instead of the single orchestrator type.

**Independent Test**: Delete `segmentation.json` for sample doc `90ec18f4`, re-run the pipeline, inspect the new file — every section has a non-empty `document_type`. Verify checklist-page findings now appear in the report.

### Implementation for User Story 1

- [ ] T002 [US1] Update `_build_segmentation_prompt` in `backend/app/compliance/segmentation.py`: remove the `key_value_pairs` parameter from the function signature and its block from the prompt body; add one instruction line with the canonical doc types loaded dynamically via `load_profiles().document_profiles.keys()` (sorted, joined); add the sub-section anchor instruction ("repeat that document's type if this section is a sub-section of a larger document already classified above")

- [ ] T003 [US1] Update `DocumentSegmenter.segment()` in `backend/app/compliance/segmentation.py`: stop forwarding `key_value_pairs` to `_build_segmentation_prompt` (the parameter stays on `segment()` for call-site compatibility with `compliance_graph.py`)

- [ ] T004 [US1] Update `build_page_to_section` in `backend/app/compliance/segmentation.py`: add `"document_type": normalize_document_type(sec.document_type) if sec.document_type else ""` to the `info` dict; import `normalize_document_type` from `app.compliance.rules.profiles`

- [ ] T005 [US1] Update `_prescreen_page` inner function in `backend/app/compliance/evaluator.py`: add `effective_doc_type = (sec_info or {}).get("document_type") or document_type` immediately after `sec_info` is resolved; replace `document_type=document_type` with `document_type=effective_doc_type` in the `gate.filter_rules_hybrid` call

- [ ] T006 [US1] Update `_run` inner function in `backend/app/compliance/evaluator.py`: add `effective_doc_type = (sec_info or {}).get("document_type") or document_type` immediately after `sec_info` is resolved; replace `document_type=document_type` in the LLM-mode `gate.filter_rules_hybrid` call and `document_type` positional arg in the static-mode `gate.filter_rules` call — all three gate call sites must use `effective_doc_type`

**Checkpoint**: US1 complete — pipeline produces `document_type` per section; evaluator applies correct rules per page.

---

## Phase 3: User Story 2 — LLM Paraphrase Resolution (Priority: P2)

**Goal**: Alias paraphrases from the LLM (e.g., `"bmr"`, `"vacuum dryer scada"`) resolve to canonical keys in the page map used by the evaluator. Raw LLM output remains visible in `segmentation.json`.

**Independent Test**: In a unit test, call `build_page_to_section` with a `DocumentSegmentation` whose sections have `document_type="bmr"` and `document_type="vacuum dryer scada"` — confirm page map entries contain `"batch_record"` and `"scada_report"` respectively.

> **Note**: T004 in Phase 2 already implements the `normalize_document_type()` call that covers this story. This phase contains only the verification tasks.

### Implementation for User Story 2

- [ ] T007 [US2] Verify `normalize_document_type` in `backend/app/compliance/rules/profiles.py` resolves all aliases listed in `document_profiles.yaml` under each profile's `aliases` key (read-only check — no code change expected; document any gap found)

**Checkpoint**: US2 complete — `build_page_to_section` resolves aliases to canonical keys; `segmentation.json` still shows raw LLM output.

---

## Phase 4: User Story 3 — Graceful Fallback for Old Cached Segmentation (Priority: P3)

**Goal**: Documents with cached `segmentation.json` files from before this feature (no `document_type` field) continue to audit without errors or score regressions.

**Independent Test**: Load the existing `segmentation.json` for sample doc `90ec18f4` (before deleting it), call `build_page_to_section`, confirm every page map entry has `document_type: ""`, then confirm the evaluator fallback logic yields the orchestrator type.

> **Note**: T001 (Pydantic default `""`) and T005/T006 (fallback chain `(sec_info or {}).get("document_type") or document_type`) already implement the mechanics. This phase contains only the verification tasks.

### Implementation for User Story 3

- [ ] T008 [US3] Confirm backward compatibility: load the existing `backend/data/documents/90ec18f4-1f29-4613-92e8-c2325bec9968/segmentation.json` (no `document_type` on sections) via `DocumentSegmentation.model_validate(json.loads(...))` — assert all sections have `document_type == ""` and no ValidationError is raised (can be a quick ad-hoc script or added to the test file)

**Checkpoint**: US3 complete — old cached files parse without error; evaluator falls back to orchestrator type for all pages.

---

## Phase 5: Tests — Rewrite Test File

**Purpose**: The existing `test_per_section_doc_type.py` was written for a field-validator design that was superseded. It must be rewritten to match the final design (normalization in `build_page_to_section`, not at model level).

- [ ] T009 Rewrite `backend/tests/compliance/test_per_section_doc_type.py` — keep the 3 model-behavior tests (empty stays empty, omitted defaults to empty, canonical passes through unchanged); remove the 4 validator-assumption tests (alias resolution, collapse-to-empty, paraphrase, all-canonical); add 6 `build_page_to_section` tests as specified in plan.md Task 5

- [ ] T010 Run `pytest backend/tests/compliance/test_per_section_doc_type.py -v` and confirm all tests pass

---

## Phase 6: Verification

**Purpose**: End-to-end validation via `quickstart.md`.

- [ ] T011 Delete `backend/data/documents/90ec18f4-1f29-4613-92e8-c2325bec9968/segmentation.json` to bust the cache

- [ ] T012 Re-run the compliance pipeline on sample doc `90ec18f4` and inspect the new `segmentation.json` — confirm every section has a `document_type` field; confirm at least one section has `operation_checklist` (pages 80–97) and one has `scada_report` (pages 48–67)

- [ ] T013 [P] Run the prompt sanity check from `quickstart.md` — confirm the segmentation prompt contains the 9 canonical keys, does not contain `section_aliases`, and does not contain KV pair text

---

## Dependencies & Execution Order

### Phase Dependencies

```
T001 (model field)
  └─→ T002, T003 (segmentation.py prompt — same file, sequential)
       └─→ T004 (segmentation.py build_page_to_section — same file, sequential)
            └─→ T005, T006 (evaluator.py — same logical block, sequential)
                 └─→ T007 (verify alias coverage — read-only)
                      └─→ T008 (verify backward compat)
                           └─→ T009, T010 (test rewrite + run)
                                └─→ T011, T012, T013 (end-to-end verification)
```

### Story Dependencies

- **US1 (P1)**: Depends on T001. No dependency on US2 or US3.
- **US2 (P2)**: Mechanically implemented by T004 (part of US1). T007 is verification only.
- **US3 (P3)**: Mechanically implemented by T001 + T005/T006 (part of US1). T008 is verification only.

### Parallel Opportunities

- T002 + T003 are logically related edits to different functions in `segmentation.py` — can be treated as one task done in sequence.
- T005 + T006 are edits to two inner functions in `evaluator.py` — do T005 first, then T006.
- T013 can run in parallel with T012 (different verification paths).
- T007 and T008 can run in parallel once T001–T006 are done.

---

## Implementation Strategy

### MVP (US1 Only — Tasks T001–T006)

1. T001: Add model field
2. T002–T003: Update prompt (drop KV pairs, add canonical types)
3. T004: Add `document_type` to page map with normalization
4. T005–T006: Wire `effective_doc_type` in evaluator
5. **Validate**: Delete cache, re-run pipeline, inspect `segmentation.json`

### Full Delivery (All Stories)

1. MVP tasks (T001–T006)
2. T007–T008: Verify alias coverage and backward compatibility
3. T009–T010: Rewrite and run tests
4. T011–T013: End-to-end verification per quickstart

---

## Notes

- Tasks T002 and T003 both modify `segmentation.py` — do them together in one edit session to avoid conflicts
- Tasks T005 and T006 both modify `evaluator.py` — same rule
- The `section_aliases` block in `document_profiles.yaml` must NOT appear in the prompt — only `profiles.document_profiles.keys()` is used
- `segmentation.json` stores raw LLM output; `build_page_to_section` returns normalized values — never conflate the two
- After T011 (cache bust), the pipeline must be triggered via the normal API route to regenerate segmentation
