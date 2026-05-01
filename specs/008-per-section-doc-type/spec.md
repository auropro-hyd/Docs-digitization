# Feature Specification: Per-Section Document Type Classification

**Feature Branch**: `008-per-section-doc-type`  
**Created**: 2026-04-29  
**Status**: Draft  
**Input**: Per-section document type classification using segmentation to enable rule applicability filtering per sub-document within a mixed-content package.

---

## Background

A document package uploaded to the compliance pipeline is typically a multi-document PDF — a Batch Manufacturing Record or Batch Production and control record stapled together with SCADA printouts, operation checklists, certificates of analysis, and IPC reports. Today:

- The orchestrator assigns one `document_type` to the entire package.
- The segmenter correctly identifies distinct sub-documents as sections, but each section carries no `document_type`.
- The applicability gate filters every rule against the single orchestrator document type for all pages.
- Rules scoped to `operation_checklist` or `scada_report` are silently skipped for the entire package — even pages that are genuinely those document types.

`document_profiles.yaml` defines 9 canonical document types anticipating mixed packages, but the pipeline has no mechanism to apply them per sub-document.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Correct Rule Filtering Per Sub-Document (Priority: P1)

A compliance reviewer submits a mixed-content document package (BPCR + checklists + SCADA + CoA). Today, checklist rules and SCADA rules are silently skipped because the orchestrator classified the whole package as `batch_record`. After this feature, those rules apply only to the pages that actually contain those sub-documents.

**Why this priority**: This is the core correctness gap. Without it, rules scoped to minority sub-documents in a package never fire — producing false compliance scores.

**Independent Test**: Submit the existing sample document (`90ec18f4`). Inspect `segmentation.json` — each section must have a non-empty `document_type` matching one of the 9 canonical keys from `document_profiles.yaml`. Then verify the compliance report shows findings from rules tagged `applicable_document_types: [operation_checklist]` on checklist pages.

**Acceptance Scenarios**:

1. **Given** a 185-page mixed package, **When** segmentation runs, **Then** `segmentation.json` contains a `document_type` field on every section, with values drawn exclusively from `document_profiles.yaml` canonical keys.
2. **Given** pages 80–97 are classified as `operation_checklist`, **When** the evaluator processes those pages, **Then** only rules with `applicable_document_types` including `operation_checklist` are applied to those pages.
3. **Given** pages 1–35 are classified as `batch_record`, **When** the evaluator processes those pages, **Then** rules scoped to `operation_checklist` are not applied to those pages.

---

### User Story 2 — LLM Paraphrase Resolution (Priority: P2)

The segmentation LLM may return a close paraphrase of a canonical type (e.g., `"batch manufacturing record"` instead of `batch_record`, or `"vacuum dryer scada"` instead of `scada_report`). The system must resolve these to the canonical key before the evaluator uses them.

**Why this priority**: Without normalization, minor LLM output variation silently breaks rule filtering — the evaluator falls back to the orchestrator type rather than raising an error.

**Independent Test**: Manually set `document_type: "batch manufacturing record"` on a section in a test fixture, run `build_page_to_section`, and confirm the output dict contains `document_type: "batch_record"`.

**Acceptance Scenarios**:

1. **Given** a section with `document_type: "batch manufacturing record"`, **When** `build_page_to_section` is called, **Then** the page map entry contains `document_type: "batch_record"` (resolved via document profile aliases).
2. **Given** a section with `document_type: "vacuum dryer scada"`, **When** `build_page_to_section` is called, **Then** the page map entry contains `document_type: "scada_report"`.
3. **Given** a section with `document_type: "logbook"` (unrecognized), **When** `build_page_to_section` is called, **Then** the page map entry contains `document_type: "logbook"` (unchanged — evaluator falls back to orchestrator type via empty-string check).

---

### User Story 3 — Graceful Fallback for Old Cached Segmentation (Priority: P3)

Documents processed before this feature have `segmentation.json` files without a `document_type` field. The system must not break for these documents — it must continue to apply the orchestrator's document type across all pages.

**Why this priority**: Production documents already have cached segmentation. Any regression here breaks re-audits of existing documents.

**Independent Test**: Load the existing `segmentation.json` for the sample document (which has no `document_type` fields), run `build_page_to_section`, and confirm every page map entry has `document_type: ""`. Then confirm the evaluator falls back to `orch_result.document_type` for those pages.

**Acceptance Scenarios**:

1. **Given** a `segmentation.json` with no `document_type` field on sections, **When** the model deserializes it, **Then** `DocumentSection.document_type` defaults to `""` without error.
2. **Given** `document_type: ""` in a page map entry, **When** the evaluator computes `effective_doc_type`, **Then** it uses the orchestrator's document type as the fallback.

---

### Edge Cases

- What happens when the LLM assigns an unrecognized `document_type` that has no alias? → Stored as-is in `segmentation.json`; evaluator fallback chain picks up orchestrator type.
- What happens when segmentation fails entirely (LLM error)? → Single-section fallback with `section_type="unknown"` and `document_type=""` — evaluator uses orchestrator type for all pages.
- What happens when a section spans pages that logically belong to different canonical types? → LLM is instructed to pick one type per section boundary. The instruction "if this section is a sub-section of a larger document already classified above, repeat that document's type" handles BMR sub-sections.
- What happens when `document_profiles.yaml` adds a new canonical type? → The prompt updates automatically (dynamic join from `profiles.document_profiles.keys()`). No code change needed.

---

## Clarifications

### Session 2026-04-29

- Q: What is the intended relationship between `document_type` and the existing `section_type` field? → A: Both coexist independently as complementary classifiers. `section_type` is the fine-grained sub-section classifier (e.g., `material_dispensing`, `cover_page`) used by `applicable_section_types` rules. `document_type` is the coarser sub-document classifier (e.g., `batch_record`, `operation_checklist`) used by `applicable_document_types` rules. The structural relationship between them is defined in `document_profiles.yaml` — each document profile's `expected_sections` list enumerates the valid `section_type` values within that document type. Both fields coexist on `DocumentSection`, both flow through the page map, and both are used independently by the applicability gate.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `DocumentSection` MUST include a `document_type: str = ""` field persisted in `segmentation.json`.
- **FR-002**: The segmentation prompt MUST include the list of canonical document type keys derived dynamically from `document_profiles.yaml` at runtime — not hardcoded.
- **FR-003**: The segmentation prompt MUST NOT include the `key_value_pairs` block (removed to reduce noise in document type classification).
- **FR-004**: The segmentation prompt MUST instruct the LLM that if a section is a sub-section of a larger document already classified above, it should repeat that document's type (anchors BMR sub-sections to `batch_record`).
- **FR-005**: `build_page_to_section` MUST normalize `document_type` via `normalize_document_type()` using the aliases defined in `document_profiles.yaml` before populating the page map.
- **FR-006**: The evaluator MUST compute `effective_doc_type` per page as: section `document_type` (from page map) → `""` (unresolved) → orchestrator `document_type` (fallback). This chain MUST be applied at all three `gate.filter_rules*` call sites.
- **FR-007**: Old `segmentation.json` files without `document_type` MUST deserialize without error, with all sections defaulting to `document_type: ""`.
- **FR-008**: Adding a new document type to `document_profiles.yaml` MUST automatically reflect in the segmentation prompt without any code change.
- **FR-009**: The `section_aliases` section of `document_profiles.yaml` MUST NOT be included in the segmentation prompt — only canonical document type keys are passed.
- **FR-010**: `section_type` classification MUST remain unchanged. `document_type` is an additive field — it does not replace or alter how `section_type` is produced, normalized, or used by `applicable_section_types` rule filtering. Both classifiers operate independently on the same `DocumentSection`.

### Key Entities

- **DocumentSection**: Extended with `document_type: str = ""`. The existing `section_type` field is unchanged. Raw LLM output for `document_type` stored in `segmentation.json`; normalized value used in page map.
- **`document_type`**: Coarse-grained sub-document classifier (e.g., `batch_record`, `operation_checklist`). Maps to the top-level profile keys in `document_profiles.yaml`. Used by `applicable_document_types` rule filtering.
- **`section_type`**: Fine-grained sub-section classifier (e.g., `material_dispensing`, `cover_page`). Unchanged by this feature. Used by `applicable_section_types` rule filtering. The valid `section_type` values within each `document_type` are defined in that profile's `expected_sections` list in `document_profiles.yaml`.
- **Page Map** (`dict[int, dict]`): Per-page lookup used by evaluator. Now includes both `section_type` (existing, normalized) and `document_type` (new, normalized) keys.
- **`effective_doc_type`**: A local variable in the evaluator computed per page from the page map, with fallback to orchestrator type. Not persisted.
- **`document_profiles.yaml`**: Single source of truth for canonical document type keys, their aliases, and their expected section types. Governs both prompt construction and output normalization.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every section in `segmentation.json` for a newly processed document contains a `document_type` field.
- **SC-002**: For the existing 185-page sample document, at least 8 of the 9 canonical document types present in the package are correctly assigned to their respective sections after re-segmentation.
- **SC-003**: Rules scoped to `applicable_document_types: [operation_checklist]` produce findings on checklist pages — findings that were previously absent due to the single-type fallback.
- **SC-004**: Existing documents with cached `segmentation.json` (no `document_type` field) continue to audit without errors or score regressions.
- **SC-005**: Adding a new canonical document type to `document_profiles.yaml` is reflected in the segmentation prompt without any code modification.

---

## Assumptions

- `document_profiles.yaml` is the single source of truth for valid document types. No hardcoded canonical type lists exist in code.
- The segmentation LLM already has sufficient page content context (first 500 chars per page + filename) to classify sub-documents correctly when given the canonical type list.
- One unified compliance report is produced — no per-sub-document splitting.
- Findings continue to be tagged by agent + page, not by sub-document type.
- Rule YAML (`applicable_document_types`, `applicable_section_types`) is unchanged.
- `document_type` normalization in `build_page_to_section` (not at the model level) is the chosen approach — `segmentation.json` stores raw LLM output for inspection; the page map uses normalized values.
- The `key_value_pairs` parameter remains on `DocumentSegmenter.segment()` for call-site compatibility but is no longer forwarded to the prompt builder.

---

## Out of Scope

- Changes to any rule YAML files.
- Changes to `document_profiles.yaml`.
- Changes to `applicability.py`.
- Changes to report models or finding structure.
- Per-sub-document compliance report splitting.
- UI changes to display per-section document types.
