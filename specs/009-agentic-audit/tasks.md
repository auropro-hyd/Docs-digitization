# Tasks: Agentic Audit Evaluation Strategy

**Feature**: `009-agentic-audit` | **Branch**: `009-agentic-audit` | **Plan**: [plan.md](plan.md)  
**Input**: Design documents from `specs/009-agentic-audit/`  
**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no shared state dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Exact file paths included in all task descriptions

## Path Conventions

All source paths under `backend/app/`. Tests under `backend/tests/`.

---

## Phase 1: Setup (Verify Structure)

**Purpose**: Confirm target files exist and understand current state before modifying anything

- [X] T001 Read and confirm current state of `backend/app/compliance/agentic/summarizer.py`, `backend/app/compliance/agentic/toolbox.py`, `backend/app/compliance/agentic/graph.py`, `backend/app/compliance/agentic/postpass.py` — note existing API signatures

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: New `compliance/summarizer.py` module and refactored `toolbox.py` — both must be complete before graph/agent refactors can proceed

**⚠️ CRITICAL**: Steps 3–6 all depend on these two files being done first

- [X] T00 Create `backend/app/compliance/summarizer.py` with three public functions: `load_summary(doc_id, doc_type, sec_type) -> str | None`, `store_page_summary(doc_id, page_num, doc_type, sec_type, text) -> None`, `async summarize_pages_in_batches(extractions, section_map, doc_id, llm, batch_size=10) -> None`; private `_summaries_file(doc_id)` returning `Path(get_settings().storage.base_path) / doc_id / "summaries" / "page_summaries.json"`; `load_summary` loads the JSON file, filters by `(doc_type, section_type)`, sorts by page_num (int), joins with `"\n\n"`, returns `None` if no matches or file absent; `store_page_summary` does read-merge-write (creates dir if needed), sets `data[str(page_num)] = {"text": text, "doc_type": doc_type, "section_type": sec_type, "generated_at": datetime.utcnow().isoformat() + "Z"}`; `summarize_pages_in_batches` loads file once, skips existing page_num keys, generates missing pages in batches of 10 via `asyncio.gather`, calls `store_page_summary` per result; fixed system prompt: `"Summarize this pharmaceutical document page in 3-5 sentences. Focus on:\n- Which section/form this page belongs to\n- Key data fields (material names, quantities, dates, operator names)\nBe concise. Preserve specific values. This summary is consumed by a compliance audit agent — accuracy over brevity."` stored as `_PAGE_SUMMARY_SYSTEM` module constant
- [X] T00 Refactor `backend/app/compliance/agentic/toolbox.py`: remove `summarizer: SummaryCapability` and `doc_dir: Path` params from `ContextToolbox.__init__`; add `doc_id: str` param; store as `self._doc_id`; add `self._summary_cache: dict[tuple[str, str | None], str] = {}`; remove `pre_generate_summaries` method entirely; update `get_context_summary(document_type, section_type=None) -> str` to check cache first, then call `load_summary(self._doc_id, document_type, section_type)` from `compliance.summarizer`, cache result, return `""` if `None`; remove `SummaryCapability` import; add `from app.compliance.summarizer import load_summary`

**Checkpoint**: Foundation ready — graph, postpass, and agent refactors can now proceed

---

## Phase 3: US1 — Agentic Graph Refactor (Package Completeness)

**Goal**: Enable an agentic audit rule to evaluate package completeness using `doc_id` throughout, replacing `doc_dir`; graph becomes 3-node topology

**Independent Test**: Invoke `run_agentic_postpass(doc_id="test-id", ...)` with a package-completeness rule; verify `RuleBatchResult` is returned with no `TypeError` on missing `doc_dir`; `fan_out_workers` returns `list[Send]` when chunks exist and `[Send("synthesize", state)]` when none

- [X] T00 [US1] Refactor `backend/app/compliance/agentic/graph.py`: in `AgenticAuditState` TypedDict remove `doc_dir: Path` and `summarizer: SummaryCapability` fields, add `doc_id: str`; remove `section_chunks` field (chunking is now inside `fan_out_workers`); replace `gather_context` node + `route_to_workers` routing function with a single `fan_out_workers(state: AgenticAuditState) -> list[Send]` routing function that: (1) builds `ContextToolbox(state["all_extractions"], state["section_map"], state["doc_id"], state["page_cap"])`, (2) calls private `_group_by_section(state["all_extractions"], state["section_map"], rule)` and `_chunk_sections(groups, state["worker_page_limit"])` (extracted from current `gather_context` body), (3) returns `[Send("synthesize", state)]` if no chunks, else `[Send("section_worker", {**state, "current_chunk": chunk, "toolbox": toolbox}) for chunk in chunks]`; rewire graph: `builder.add_conditional_edges(START, fan_out_workers, ["section_worker", "synthesize"])`, keep `builder.add_edge("section_worker", "synthesize")`, `builder.add_edge("synthesize", END)`; remove `gather_context` node registration; remove `SummaryCapability` import; remove `from pathlib import Path` if unused
- [X] T00 [US1] Refactor `backend/app/compliance/agentic/postpass.py`: change signature from `doc_dir: Path` to `doc_id: str`; in state construction use `doc_id=doc_id` instead of `doc_dir=doc_dir, summarizer=summarizer`; remove `SummaryCapability()` instantiation; remove `from pathlib import Path` import if unused; remove `SummaryCapability` import; ensure `section_chunks=[]` is removed from initial state if `section_chunks` was removed from `AgenticAuditState`

**Checkpoint**: Agentic graph runs end-to-end with `doc_id` param; US1 package-completeness rules are evaluable

---

## Phase 4: US2 — Agent Wiring (Cross-Section Traceability)

**Goal**: All three agents (ALCOA, GMP, Checklist) call `run_agentic_postpass(doc_id=...)` — enabling cross-section rules to be configured and executed

**Independent Test**: Call `alcoa_agent.review_document(extractions, doc_id="pkg-001")` — verify no `TypeError` about `doc_dir`; postpass invoked with `doc_id="pkg-001"`

- [X] T00 [P] [US2] Refactor `backend/app/compliance/alcoa.py`: remove `doc_dir: Path | None = None` from `review_document()` signature (keep `doc_id: str | None = None`); change postpass call from `doc_dir=doc_dir or Path(".")` to `doc_id=doc_id or ""`; remove `from pathlib import Path` if no longer used anywhere in the file
- [X] T00 [P] [US2] Refactor `backend/app/compliance/gmp.py`: same changes as T006 — remove `doc_dir` param from `review_document()`, change postpass call to `doc_id=doc_id or ""`, remove unused `Path` import
- [X] T00 [P] [US2] Refactor `backend/app/compliance/checklist.py`: same changes as T006 — remove `doc_dir` param from `review_document()`, change postpass call to `doc_id=doc_id or ""`, remove unused `Path` import

**Checkpoint**: All three agents accept `doc_id`, no `doc_dir` parameter; cross-section rules (US2) can be invoked via the standard `review_document` API

---

## Phase 5: US2 — Summarization Pipeline (Performance Enablement)

**Goal**: Phase 1.5 page summarization in `compliance_graph.py` ensures summaries are on disk before agentic graph runs, enabling summary-first context retrieval for cross-section rules

**Independent Test**: With `enable_cross_page=True`, run `compliance_graph.process_document(doc_id="test")` through the segmentation phase; verify `{doc_id}/summaries/page_summaries.json` is created and contains entries for each page; verify existing pages are skipped on a second run

- [X] T00 [US2] Add Phase 1.5 page summarization block to `backend/app/workflow/compliance_graph.py`: locate the `if config.enable_cross_page:` block that follows `section_map = build_page_to_section(segmentation)` at approximately line 159; immediately after the `section_map` assignment, insert: `from app.compliance.summarizer import summarize_pages_in_batches` (local import to avoid circular dep), then `await _ws_progress(doc_id, {"phase": "summarization", "status": "running", "label": f"Generating page summaries ({len(extractions)} pages)..."})`, then `summ_llm = container.compliance_cross_page_llm`, then `await summarize_pages_in_batches(extractions, section_map, doc_id, summ_llm)`, then `await _ws_progress(doc_id, {"phase": "summarization", "status": "complete", "label": "Page summaries ready"})`; do NOT change any agent call-sites — they already pass `doc_id`

**Checkpoint**: Summaries pre-generated before agents run; agentic workers get summary-first context for any cross-section rule (US2)

---

## Phase 6: US3 — Cleanup & Robustness

**Goal**: Remove stale code (`agentic/summarizer.py`, `summary_profiles.yaml`) and fix the broken script kwarg — ensures the system fails explicitly rather than silently when old paths are used

**Independent Test**: Import `from app.compliance.agentic.summarizer import SummaryCapability` should raise `ImportError`; running `backend/scripts/run_checklist_agentic_postpass.py` with a valid `doc_id` should not raise `TypeError` about unexpected `segmentation` kwarg

- [X] T01 [US3] Delete `backend/app/compliance/agentic/summarizer.py` and `backend/app/compliance/rules/summary_profiles.yaml`; scan all remaining Python files under `backend/app/compliance/agentic/` for any remaining `import SummaryCapability` or `from .summarizer import` lines and remove them (should be none after T003–T005)
- [X] T01 [US3] Fix `backend/scripts/run_checklist_agentic_postpass.py`: remove the stale `segmentation=seg` keyword argument from the `run_agentic_postpass(...)` call; verify `doc_id=doc_id` kwarg is present and correct; no other changes

**Checkpoint**: `SummaryCapability` is gone from the codebase; standalone script works without signature mismatch; US3 graceful-fallback behaviour is correct because `get_context_summary` returns `""` when no summary file exists, workers fall back to raw pages (per FR-016)

---

## Phase 7: Polish — Test Updates

**Purpose**: Update test file to match refactored APIs; no new behaviour introduced

- [X] T01 Update `backend/tests/compliance/test_agentic_audit.py`: (1) replace `from app.compliance.agentic.summarizer import SummaryCapability` with `from app.compliance.summarizer import load_summary, store_page_summary`; (2) remove `gather_context` import; add `fan_out_workers` import from `app.compliance.agentic.graph`; (3) in `ContextToolbox` fixtures replace `doc_dir=tmp_path, summarizer=...` with `doc_id="test-doc"`; (4) in `AgenticAuditState` fixtures replace `doc_dir=Path("."), summarizer=SummaryCapability()` with `doc_id="test-doc"`; (5) in `run_agentic_postpass` calls in tests replace `doc_dir=Path(".")` with `doc_id=""`; (6) replace any `gather_context` node tests with `fan_out_workers` routing tests that verify `list[Send]` is returned; (7) update `store_summary` / `load_summary` tests to pass `doc_id` param and monkeypatch `get_settings().storage.base_path` to `str(tmp_path)` so storage resolves under `tmp_path / "test-doc" / "summaries" / "page_summaries.json"`

---

## Dependencies

```text
T001 → T002, T003                  (read current state before modifying)
T002 → T003                        (toolbox imports load_summary)
T002, T003 → T004                  (graph imports ContextToolbox)
T004 → T005                        (postpass imports AgenticAuditState)
T005 → T006, T007, T008            (agents call run_agentic_postpass with new sig)
T002 → T009                        (compliance_graph imports summarize_pages_in_batches)
T003, T004, T005 → T010            (delete summarizer only after all consumers updated)
T005 → T011                        (fix script after postpass sig is final)
T006–T011 → T012                   (update tests last, after all source changes are done)
```

**Parallel opportunities**:
- T006, T007, T008 run in parallel (different files, same pattern)
- T009 runs independently of T006–T008 (compliance_graph.py is separate from agents)
- T010, T011 run in parallel (different files)

---

## Implementation Strategy

**MVP** (delivers US1 end-to-end): T001 → T002 → T003 → T004 → T005 → T006 → T010 → T011 → T012  
**Full** (all user stories): complete all phases sequentially per the dependency graph above

All tasks touch existing Python files. No new endpoints, no schema migrations, no new config fields beyond those already documented in `settings.py`.

---

## Summary

| Phase | Tasks | Description |
|-------|-------|-------------|
| Setup | T001 | Read current state |
| Foundational | T002–T003 | `compliance/summarizer.py` + `toolbox.py` refactor |
| US1 (P1) | T004–T005 | Graph + postpass refactor (`doc_dir` → `doc_id`) |
| US2 (P2) | T006–T009 | Agent wiring + Phase 1.5 summarization |
| US3 (P3) | T010–T011 | Delete stale files, fix script |
| Polish | T012 | Test updates |

**Total tasks**: 12  
**Parallel opportunities**: T006/T007/T008 in parallel; T009 independent of agents; T010/T011 in parallel  
**Estimated scope**: ~8 sequential execution steps after accounting for parallel groups
