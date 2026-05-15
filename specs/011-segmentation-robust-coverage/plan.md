# Implementation Plan: Robust Segmentation Coverage and Consistency

**Branch**: `011-segmentation-robust-coverage` | **Date**: 2026-05-14 | **Spec**: [./spec.md](./spec.md)
**Input**: Feature specification from `/specs/011-segmentation-robust-coverage/spec.md`

## Summary

The compliance pipeline starts with segmentation; every downstream artefact is keyed on it. PR #69 closed the geometric and vocabulary failure modes. This spec applies inversion thinking to catch the five classes of failure still left:

1. **Page-header boundary respect** — parse `Page X of Y` from OCR markdown; merge LLM-split sections, split LLM-glued sections (US1).
2. **Output-truncation detection** + **structural minimums** — detect coverage shortfall and required-section absence; retry tail when truncated (US2).
3. **Cross-evidence validators** — KV-pair coverage + section_type/document_type consistency (US3).
4. **HITL-edit preservation** — sidecar overrides file survives any number of re-segmentations (US4).
5. **Surface validators in HITL response** — add `validation_issues[]` to `GET /segmentation` so the editor can render warnings (FR-014, cross-cutting).

All new logic is deterministic post-process running after `clamp_page_ranges` and `resolve_overlaps` from PR #69. The truncation-retry path (US2) issues a second LLM call only when a quantitative coverage shortfall (<97%) is detected; capped at 2 retries.

## Technical Context

**Language/Version**: Python 3.13 (backend), TypeScript 5 / Next.js 15 (frontend — wire-only change in this spec)
**Primary Dependencies**: FastAPI, Pydantic, PyYAML, structlog, existing LLM provider stack (`app.core.ports.llm`)
**Storage**: JSON files under `data/documents/{doc_id}/` — new sidecar `segmentation.overrides.json`
**Testing**: pytest, unit + integration tiers (`backend/tests/compliance/`, `backend/tests/integration/`)
**Target Platform**: Linux server (FastAPI + uvicorn), runs alongside the existing compliance pipeline
**Project Type**: Web application — backend post-process work in this spec; no frontend change
**Performance Goals**: New post-processes are O(sections × pages) on small in-memory data; <50 ms added per segmentation on the happy path
**Constraints**: Truncation retry MUST cap at 2 LLM calls to bound cost; coverage threshold tunable via single constant
**Scale/Scope**: Up to ~200 pages per packet, ~50 sections per segmentation, ~3000 KV pairs per doc

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (v1.1.0).

- [x] **I. Leverage-first**: Plan extends the existing segmentation pipeline (`DocumentSegmenter`, `validate_segmentation`, `fill_gaps_with_unknown`) with new deterministic post-processes. No subsystem is replaced. The truncation-retry uses the existing LLM port; the overrides sidecar reuses the existing `segmentation.json` storage shape.
- [x] **II. 5-stage soft gates + parallel compliance**: All work sits inside the **Legibility & Classification** stage (segmentation runs there today). No new mid-pipeline HITL. The `validation_issues` array surfaces in the existing segmentation editor's read endpoint; no new HITL gates.
- [x] **III. Capability-first**: Each post-process is an independently callable pure function (`parse_page_headers`, `group_boundary_units`, `merge_split_by_boundary`, `detect_truncation`, `apply_overrides`, `validate_cross_evidence`). Unit-testable in isolation.
- [x] **IV. Single final checkpoint & selective re-run**: No new findings emitted by this spec; the existing compliance findings reach the same checkpoint. The truncation retry is internal — it doesn't surface as a re-run-scoped HITL action.
- [x] **V. Evidence-bound findings**: Not applicable — this spec emits validation_issues, not compliance findings. Each issue still carries `section_ids` and `page_range` so HITL knows where to look.
- [x] **VI. Configurable framework**: Required-section logic reads from existing `document_profiles.yaml`. No client-specific layouts hardcoded in Python. Coverage threshold (`_TRUNCATION_COVERAGE_THRESHOLD`) lives as a module-level constant; tunable without rebuild.
- [x] **VII. Existing framework is the backbone**: Builds on `app.compliance.segmentation`, `app.compliance.rules.profiles`, `app.observability.run_telemetry`. Adds no new packages. Regression tests cover the PR #69 post-processes that the new pipeline composes with.
- [x] **VIII. ALCOA+ audit trail**: `SegmentationOverride` records `recorded_at` (UTC) and `actor` (operator identity from the HITL request). The overrides sidecar is append-only in spirit — we keep history; last-write-wins on apply.
- [x] **IX. Rule-as-data**: The structural-minimums check (FR-009) reads `required: true` from `document_profiles.yaml` — declarative, not code-baked. New rule logic introduced: none. Existing rule-spec schema is untouched.

All checks pass; no entries in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/011-segmentation-robust-coverage/
├── plan.md              # This file
├── research.md          # Inversion analysis + design choices
├── data-model.md        # PageHeader, BoundaryUnit, SegmentationOverride, ValidationIssue kinds
├── quickstart.md        # Smoke tests on the user's 2026-05-13 doc
├── contracts/
│   └── api-contract.md  # GET/PUT /segmentation, POST /segment shape changes
├── checklists/
│   └── requirements.md  # PR-review checklist
└── tasks.md             # Numbered tasks by user story
```

### Source Code

```text
backend/app/compliance/
├── segmentation.py                  # NEW functions added; pipeline wiring extended
├── segmentation_headers.py          # NEW — page-header parser + boundary unit grouper (FR-001, FR-002)
├── segmentation_overrides.py        # NEW — load/save sidecar + apply (FR-012, FR-013)
└── rules/
    ├── document_profiles.yaml       # No content change; FR-009 reads existing required: true fields
    └── profiles.py                  # No change; helper `required_sections_for` already extractable

backend/app/api/routes/compliance.py # PUT /segmentation: append to overrides file
                                      # GET /segmentation: include validation_issues
                                      # POST /segment: apply overrides after LLM call

backend/tests/compliance/
├── test_segmentation_headers.py     # NEW — parse_page_headers, group_boundary_units
├── test_segmentation_boundary_merge.py  # NEW — FR-003, FR-004
├── test_segmentation_truncation.py  # NEW — FR-006, FR-007, FR-008
├── test_segmentation_structural_min.py  # NEW — FR-009
├── test_segmentation_validators.py  # NEW — FR-010, FR-011
└── test_segmentation_overrides.py   # NEW — FR-012, FR-013

backend/tests/integration/
└── test_segmentation_endpoint.py    # NEW — PUT /segmentation persists; GET surfaces issues
```

**Structure Decision**: Single-project layout extending `backend/app/compliance/`. Three new modules keep concerns separated:

- `segmentation_headers.py` — text parsing + grouping; pure functions, no dependencies on the rest of segmentation.
- `segmentation_overrides.py` — file I/O + Pydantic model for the sidecar; isolated from the LLM path.
- `segmentation.py` (existing) — gains four pure-function additions and is rewired with the new pipeline order from `data-model.md`.

## Implementation Phases

### Phase 1 — Setup (shared infrastructure)

Cheap groundwork: new modules, dependency-free pure helpers, schema extension on `DocumentSegmentation`.

### Phase 2 — Foundational (US1: page-header boundary respect)

Parse OCR markdown → `PageHeader[]` → `BoundaryUnit[]` → boundary-aware merge / split post-process. Plumb into `DocumentSegmenter.segment()` between `fill_gaps_with_unknown` and `normalize_section_types_to_canonical`.

### Phase 3 — Output-truncation detection + structural minimums (US2)

Implement coverage-shortfall detector and the LLM retry path. Add `required-section` check that reads `document_profiles.yaml`'s `required: true` flag. Emit telemetry; do not hard-fail.

### Phase 4 — Cross-evidence validators (US3)

`no_kv_evidence` and `type_mismatch` validators. Pure functions over `DocumentSegmentation` + the input `key_value_pairs`. Hooked into `validate_segmentation` so the existing telemetry event-emit loop picks them up.

### Phase 5 — HITL-edit preservation (US4)

Sidecar load/save + apply step. Wire into `PUT /segmentation` (write) and `DocumentSegmenter.segment()` (read+apply). Orphaned-override handling.

### Phase 6 — Surface validators in HITL response (FR-014)

Add `validation_issues: list[SegmentationIssueDict]` to the `DocumentSegmentation` model; populate in `DocumentSegmenter.segment()`. Persist to `segmentation.json` so the editor reads them on subsequent `GET /segmentation` calls. Frontend rendering is a separate PR.

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Page-header parser too tolerant — false-positive merges (`Page 1 of 3` in body text) | Medium | Restrict parser to first ~200 chars of each page's markdown (running-header location). False positives still possible on header-heavy first pages, but the `header_count < expected_pages` signal flags them. |
| Truncation-retry causes runaway LLM cost on a pathological doc | Low | Hard cap at 2 retries (FR-008); each retry is on a smaller range than the original call. |
| Overrides file gets stale when LLM output renames `section_id`s | Medium | `override_orphaned` event surfaces the case; operator re-applies via the editor. |
| `validation_issues` array balloons (200 issues per run) | Low | Issues are intrinsically bounded — one per section per validator. Worst case ~5×N where N=section count (<50). |
| Existing tests in `test_segmentation_robust_coverage.py` regress | Medium | Phase 1 starts by re-running the full suite; new code goes behind well-named pure-function APIs so existing tests don't see them. |

## Out of scope

- Per-section LLM confidence on the model.
- Frontend rendering of `validation_issues` in the segmentation editor.
- Visual diff of segmentation runs.
- Improvements to the BPCR sub-section heuristic detector (Spec 007 territory).
- Header-aware classification beyond `Page X of Y` (e.g. logo OCR, document-restart heuristics).

These are tagged for follow-up specs.

## Complexity Tracking

No violations of the constitution; this section is intentionally empty.
