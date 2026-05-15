# Feature Specification: Robust Segmentation Coverage and Consistency

**Feature Branch**: `011-segmentation-robust-coverage`
**Created**: 2026-05-14
**Status**: Draft
**Input**: Akhilesh's 2026-05-13 voice-notes review of segmentation on 2538105062.pdf — "missing BPCR sub-sections", "raw material section type echoing doc type", "splits page-1-of-3 docs into two or three", "unknown pages with gaps", "checklist documents merged into one section", "duplicate overlapping section entries", "section types not defined in document_profiles.yaml". PR #69 closed the most-tractable post-process bugs (overlap clamp, range clip, canonical-type fold). This spec applies INVERSION thinking — designing for everything that could *still* cause segmentation to fail — and closes the remaining failure modes deterministically.

## Background & Motivation

The compliance pipeline starts with segmentation. Every downstream artefact — rule applicability, cross-document filtering, the on-screen rule table, the PDF export — is keyed on `DocumentSegmentation.sections`. A wrong segmentation produces a wrong report regardless of how good the rest of the pipeline is. The user's 2026-05-13 review confirmed segmentation is currently the highest-leverage place to invest.

PR #69 introduced three deterministic post-processes (clamp page ranges, resolve overlaps, normalize types) and tightened prompt cues. Inversion analysis surfaces five remaining classes of failure the post-processes don't cover:

1. **Document-boundary signals from page headers are ignored.** Most pharma forms print `Page X of Y` in their running header. A 3-page raw-material request will say `1 of 3` / `2 of 3` / `3 of 3`. The LLM has to re-derive that boundary from content alone and frequently mis-splits.
2. **LLM output silently truncates.** A 150-page packet pushed through a structured-output call may hit the LLM's output cap mid-JSON. Pydantic parses what arrived; tail sections quietly disappear.
3. **Structural minimums per profile aren't enforced.** A `batch_record` segmentation with zero `cover_page` is impossible in the real document — the LLM lost it — but our pipeline accepts it.
4. **No cross-validation against the OCR's `key_value_pairs`.** KV pairs carry per-page metadata (batch numbers, product names, equipment IDs). A section claiming 10 pages with zero KV pairs across those pages is almost certainly mis-classified.
5. **Re-segmentation wipes operator HITL edits.** Whenever an operator manually corrects a boundary in the segmentation editor and the route `/api/compliance/{doc_id}/segment` is hit again, the LLM re-runs and overwrites their work.

This spec closes those five gaps in a way that's robust (LLM-independent post-process), consistent (same input → same output), and observable (every correction emits a telemetry event so HITL reviewers see what was changed and why).

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Page-header boundary respect (Priority: P1) 🎯 MVP

An operator runs segmentation on a packet whose raw-material request form prints `Page 1 of 3` / `Page 2 of 3` / `Page 3 of 3` in its running header. Even when the LLM splits those three pages into two separate sections (a common failure on form-layout shifts mid-document), the post-process detects the `X of Y` header pattern, recognises the three pages belong to the same logical document, and merges the LLM's split sections back into one.

**Why this priority**: The user described this exact failure ("page 1 of 3 means the document has 3 pages — segmentation is splitting it into two or three"). It's the highest-confidence boundary signal available — the form designer literally printed how many pages it has — and ignoring it makes the rest of the segmentation lie about document structure. P1 because the user's example is real and reproducible.

**Independent Test**: Given a 3-page raw-material form with `Page 1 of 3` … `Page 3 of 3` headers and a deliberately wrong LLM output that splits it into `{pages: 30-31, type: material_request}` and `{pages: 32, type: material_issue}`, the post-process emits a single `{pages: 30-32, type: material_request}` (or whichever sub-type wins by majority) and fires a `segmentation.header_boundary_merged` event with the original / merged spans.

**Acceptance Scenarios**:

1. **Given** a 3-page segment with consistent `Page X of 3` headers (X=1,2,3) and an LLM that emitted two sections for those pages, **When** the post-process runs, **Then** the two sections collapse to one whose page range equals the headers' range and the section_type/document_type is taken from the section with the most pages (tie-break: lowest `start_page`).
2. **Given** a 5-page segment where pages 1-3 share `1 of 3 / 2 of 3 / 3 of 3` and pages 4-5 share `1 of 2 / 2 of 2`, **When** the post-process runs, **Then** the output is exactly two sections — one for pages 1-3 and one for pages 4-5 — even if the LLM emitted them differently.
3. **Given** a page with no detectable `Page X of Y` pattern, **When** the post-process runs, **Then** the page passes through unchanged (no spurious merging or splitting).
4. **Given** an LLM output where one section spans `Page 1 of 3` and `Page 1 of 2` (two different documents glued together), **When** the post-process runs, **Then** the section splits at the page where the `Y` value changes.

---

### User Story 2 — Output-truncation detection and structural minimums (Priority: P1)

The segmentation LLM call is structured-output through `generate_structured`. For long packets (≥120 pages) the LLM may hit its output token cap mid-JSON. Pydantic still parses the partial response; the pipeline accepts a segmentation that's missing the tail of the document. With this story, the post-process detects two classes of "this output is suspicious" and fails loud rather than shipping a silently incomplete segmentation:

(a) **Coverage shortfall** — the highest section's `end_page` is materially less than `total_pages` AND the LLM's `finish_reason` (when surfaced by the provider adapter) is `length`. Treated as truncation; the pipeline retries with the doc tail and merges.

(b) **Structural minimum** — the resolved doc_type's profile declares one or more `required: true` sections in `document_profiles.yaml`; the segmentation produced zero of them. Emit a `segmentation.missing_required_section` warning and surface in the HITL queue.

**Why this priority**: The truncation case is invisible today — the run completes "successfully" with a partial answer, the operator never knows. The structural-minimum case catches the BPCR-collapsed-to-one-section regression the user described. P1 because both fail silently right now.

**Independent Test**: With a 200-page packet and an LLM stub that returns a truncated response covering only pages 1-100, the segmenter detects the shortfall, retries on the second half, and the final segmentation covers all 200 pages. With a `batch_record` doc whose LLM output omits `cover_page`, the validator emits the warning telemetry and the run summary's `missing_required_sections` field lists it.

**Acceptance Scenarios**:

1. **Given** a 200-page packet and an LLM that returns a parsed `DocumentSegmentation` whose sections cover only pages 1-100 with `finish_reason='length'`, **When** the segmenter runs, **Then** it issues a second LLM call covering pages 101-200, merges the results disjointedly, and the final coverage is 1-200.
2. **Given** a packet whose first segmentation pass covers everything but the LLM omitted a `required: true` section for the inferred doc_type, **When** validation runs, **Then** a `segmentation.missing_required_section` event fires per missing section, the run completes (no hard fail), and the issue is recorded in the segmentation result's `validation_issues` field for HITL surfacing.
3. **Given** an LLM call whose adapter doesn't expose a `finish_reason`, **When** coverage shortfall is detected, **Then** the segmenter still retries on the gap range — `finish_reason` is treated as a tie-breaker, not a precondition.

---

### User Story 3 — Cross-evidence validators (Priority: P2)

Beyond geometric validity, the segmentation must be *semantically* coherent. This story introduces two validators that run after the existing `validate_segmentation`:

(a) **KV-pair coverage** — for each section, count KV pairs whose `page_num` is in range. A section spanning ≥3 pages with zero KV pairs is suspicious (probably image-only or mis-classified). Emit `segmentation.no_kv_evidence` warning with the section_id and span.

(b) **Type-consistency** — for each section, check that `section_type` is plausibly part of `document_type`'s profile (or is `unknown`). A `manufacturing_operations` section_type with `document_type='ipc_report'` is contradictory. Emit `segmentation.type_mismatch` warning.

Both validators are warnings, not errors — the segmentation output is not mutated. HITL reviewers see the warnings in the segmentation editor and decide whether to re-run.

**Why this priority**: Catches the LLM-emitted artefacts that PR #69's vocabulary normalisation can't (it can fold an alias to canonical, but it can't tell whether a canonical value belongs to its declared doc_type). P2 because they don't change behaviour today — they just give HITL better signal.

**Independent Test**: With a section spanning pages 50-55 and zero KV pairs whose `page_num` is in that range, the validator emits the warning event. With a section whose `section_type='manufacturing_operations'` and `document_type='ipc_report'`, the validator emits the mismatch event.

**Acceptance Scenarios**:

1. **Given** a 6-page section and key_value_pairs whose page_nums are all <50 or >55, **When** validators run, **Then** a `segmentation.no_kv_evidence` event fires with section_id and page range.
2. **Given** a section with `section_type='manufacturing_operations'` (a batch_record-only type) but `document_type='ipc_report'`, **When** validators run, **Then** a `segmentation.type_mismatch` event fires naming both fields.
3. **Given** a section with `section_type='unknown'`, **When** validators run, **Then** type-consistency does NOT fire (unknown is a deliberate placeholder, not drift).

---

### User Story 4 — HITL-edit preservation across re-segmentation (Priority: P2)

When an operator manually edits the segmentation (renaming a section, adjusting page ranges, fixing a section_type) and later triggers a re-segmentation — typically because the document was re-OCR'd or because they want to see if the LLM agrees — the LLM-driven re-run currently OVERWRITES the operator's edits.

With this story, operator edits are persisted to a sidecar `segmentation.overrides.json` keyed by the original LLM-output's section identity. On every re-segmentation the pipeline reads the overrides file last and applies it on top of the LLM output before the post-processes run. The operator's work survives any number of re-segmentations.

**Why this priority**: This is workflow-quality, not output-quality — it doesn't change what segmentation does on a fresh run, just protects accumulated operator effort. P2 because the user didn't explicitly call it out, but it's the obvious follow-on to making segmentation good enough that operators care about it.

**Independent Test**: With a saved segmentation that includes an operator override (e.g. operator extended a section's `end_page` from 25 to 27), trigger a re-segmentation; the resulting `segmentation.json` shows the operator's override applied even though the fresh LLM run reported `end_page=25`.

**Acceptance Scenarios**:

1. **Given** an operator has saved an edit via `PUT /segmentation` that changes section `S`'s `end_page` from 25 to 27, **When** they later trigger `POST /segment`, **Then** the resulting segmentation has `S.end_page == 27`.
2. **Given** an operator has saved edits to two sections and re-segmentation produces an LLM output that no longer contains one of those section_ids, **When** the merge runs, **Then** the surviving override applies and the missing one is logged as `segmentation.override_orphaned` (the original target no longer exists; HITL must re-decide).
3. **Given** no overrides file exists, **When** re-segmentation runs, **Then** behaviour is identical to today.

---

### Edge Cases

- **Mis-detected `Page X of Y`**: OCR-noisy headers like `Pege 1 of 3` (typo) — the parser uses a tolerant regex that allows `[Pp]a?ge` and accepts the parse, but emits a `segmentation.header_low_confidence` event for HITL review.
- **`Page 1 of 1` single-page documents**: the parser treats them as their own one-page boundary; no merging needed.
- **Single-page segments with no header**: the boundary parser leaves them alone; the LLM's classification stands.
- **Headers conflicting across pages**: page 30 says `1 of 3` and page 31 says `1 of 5`. The parser splits the boundary at page 31 (the `Y` value changed) and emits `segmentation.boundary_conflict`.
- **LLM output empty / malformed**: the structured-output adapter raises; the segmenter catches, logs, and returns a single `unknown` section covering all pages so the rest of the pipeline doesn't crash.
- **Retry on truncation that ALSO truncates**: after two retries on the same gap range, give up and emit `segmentation.retry_exhausted` with the still-uncovered range filled as `unknown`. Better to surface the gap than loop forever.
- **Override targets a section that no longer exists**: the override is logged as orphaned (US4 AS-2); HITL decides.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST parse `Page X of Y` (and tolerant variants `Page X/Y`, `pg X of Y`, with optional whitespace and case-insensitive `[Pp]a?ge`) from the OCR markdown of each page; the parse result is `(X: int, Y: int)` or `None`.
- **FR-002**: System MUST group consecutive pages sharing the same `Y` value and starting at `X=1` (with `X` strictly increasing thereafter) into a "header-attested boundary unit".
- **FR-003**: System MUST, as a post-process AFTER existing `clamp_page_ranges`, `resolve_overlaps`, and `fill_gaps_with_unknown`, merge LLM segmentation sections whose page ranges fall entirely within one boundary unit into a single section. The merged section's `section_type` / `document_type` come from the constituent section that covers the most pages (tie-break: lowest `start_page`).
- **FR-004**: System MUST split an LLM section that spans a boundary-unit transition (e.g. starts at `Page 1 of 3` and ends at `Page 1 of 2`) at the page where `Y` changes.
- **FR-005**: System MUST emit a `segmentation.header_boundary_merged` event per merge with `from_sections` (list of original section_ids + ranges) and `to_range`, and a `segmentation.header_boundary_split` event per split.
- **FR-006**: System MUST detect "output truncation" via a coverage shortfall: when the highest `end_page` across emitted sections is less than `total_pages * 0.97`. The 0.97 threshold tolerates a 3% page-classification gap from `fill_gaps_with_unknown` without false-positive-ing every run.
- **FR-007**: System MUST, on detected truncation, issue ONE retry LLM call covering only the uncovered tail (`uncovered_start` to `total_pages`) and merge the result into the original output via the same disjoint-page-range guarantee.
- **FR-008**: System MUST cap the truncation retry chain at 2 attempts; after the second, fill the still-uncovered range with `section_type='unknown'` and emit `segmentation.retry_exhausted`.
- **FR-009**: System MUST load `expected_sections` with `required: true` from `document_profiles.yaml`; for each segmentation, compute per-document_type the set of required section_types and the set of emitted section_types; emit `segmentation.missing_required_section` for every required type absent from the emitted set.
- **FR-010**: System MUST validate cross-evidence: for each section spanning ≥3 pages, count `key_value_pairs` whose `page_num` falls in `[start_page, end_page]`; when the count is zero, emit `segmentation.no_kv_evidence` with the section_id and span.
- **FR-011**: System MUST validate type-consistency: for each section where both `section_type` and `document_type` are non-empty and section_type is not `unknown`, check that the section_type appears in the profile's `expected_sections` (or aliases) for that document_type; emit `segmentation.type_mismatch` when it does not.
- **FR-012**: System MUST persist operator edits made via `PUT /api/compliance/{doc_id}/segmentation` to a sidecar file `data/documents/{doc_id}/segmentation.overrides.json` whose schema records the LLM-output section identity, the field overridden, and the new value.
- **FR-013**: System MUST, on every `POST /api/compliance/{doc_id}/segment` re-run, apply persisted overrides on top of the LLM output BEFORE running the geometric / vocabulary post-processes. An override whose target section is missing from the new LLM output emits a `segmentation.override_orphaned` event and is dropped.
- **FR-014**: System MUST surface all `segmentation.*` warning-level events in the segmentation HITL response (`GET /api/compliance/{doc_id}/segmentation`) as a `validation_issues` array so the editor UI can flag them to the operator.

### Key Entities

- **PageHeader**: `(page_num: int, x: int, y: int, raw: str, confidence: float)`. Output of FR-001; one per page that carries a detectable header. `confidence` reflects parser tolerance — 1.0 for `Page X of Y`, 0.7 for the tolerated typo variants.
- **BoundaryUnit**: `(start_page: int, end_page: int, expected_pages: int, header_count: int)`. Output of FR-002. `header_count` equals how many of `expected_pages` actually carried a parseable header (the rest were inferred from the run).
- **SegmentationOverride**: `(section_id: str, field: Literal["section_type", "document_type", "start_page", "end_page", "name"], value: str | int, recorded_at: datetime, actor: str)`. One record per field changed by HITL.
- **ValidationIssue** (extension of existing `SegmentationIssue`): adds `header_boundary_merged`, `header_boundary_split`, `header_low_confidence`, `boundary_conflict`, `output_truncated`, `retry_exhausted`, `missing_required_section`, `no_kv_evidence`, `type_mismatch`, `override_orphaned` kinds.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On the user's 2026-05-13 doc (`2538105062.pdf`, 115 pages), the post-processed segmentation contains zero overlapping page ranges, zero pages outside `[1, 115]`, zero sections with `section_type='unsectioned'`. (Already met by PR #69 for the geometric / vocabulary checks; this spec extends to the new validators.)
- **SC-002**: On the same doc, every multi-page raw-material sub-form collapses into one section per `Page X of Y`-attested boundary unit. Measured: the number of distinct sections within the `raw_material_request` doc_type equals the number of detected boundary units.
- **SC-003**: For any 200-page synthetic packet, the post-process detects an LLM-truncated output and recovers full coverage in ≤2 retry attempts. Measured: integration test with a stubbed LLM that truncates at page 100 produces a final segmentation covering 1-200.
- **SC-004**: For a `batch_record` whose LLM output omits `cover_page`, a `segmentation.missing_required_section` event fires; the validation issue is visible in the `GET /segmentation` response.
- **SC-005**: An operator's HITL edit to a section's `end_page` survives a subsequent `POST /segment` re-run, evidenced by inspecting `segmentation.json` after the re-run.
- **SC-006**: HITL surface (segmentation editor) shows the new `validation_issues` array on every load; reviewers can act on it without leaving the page.

## Assumptions

- **OCR markdown is the only header source.** We don't have explicit `page_header` / `page_footer` extraction from the OCR adapter. Page-header parsing operates on the first ~200 chars of each page's markdown (the running-header location) and tolerates layout variation. If a future OCR adapter exposes structured header zones, the parser will prefer them — the post-process API stays the same.
- **Operator overrides are coarse-grained.** Overrides are field-level, not character-level. Renaming a section to a custom string, adjusting integer pages, or changing the section_type/document_type are supported; partial-page splits are not.
- **The structured-output LLM adapter handles JSON parsing.** When `generate_structured` raises (malformed JSON, schema mismatch), we treat the whole call as failed and fall back to a single `unknown`-section segmentation — the same defensive shape PR #69 added for the retry-exhausted case.
- **`finish_reason` may not be available.** Some adapters in the stack don't surface a finish_reason. The truncation detector treats coverage shortfall as the primary signal; `finish_reason` is consulted when available but isn't required.
- **`document_profiles.yaml` is authoritative.** The `required: true` flag on `expected_sections` is the source-of-truth for structural minimums. Profile authors are responsible for keeping it accurate; the spec does not introduce additional config.
- **Performance budget.** All new post-processes are pure CPU work on small in-memory structures (<200 sections, <500 pages). No re-LLM-ing except the truncation retry path (US2), which is opt-in and capped at 2 attempts. Net added latency on the happy path is negligible (<50ms).
- **No frontend changes in this spec.** Surfacing `validation_issues` in the segmentation editor UI is a follow-up. The endpoint change in FR-014 is wire-only; the editor can pick it up incrementally.
