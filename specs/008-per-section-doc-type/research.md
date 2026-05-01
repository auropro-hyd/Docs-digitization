# Research: Per-Section Document Type Classification

**Date**: 2026-04-29  
**Status**: Complete — no external unknowns

## Findings

### Decision: Normalization location

**Decision**: Normalize `document_type` in `build_page_to_section`, not as a Pydantic field validator on `DocumentSection`.

**Rationale**: The user wants `segmentation.json` to reflect raw LLM output for inspection and debugging. `build_page_to_section` is the consumer-facing boundary — only the evaluator reads from it, and it already normalizes `section_type` in the same function. Consistent pattern, no model-level side effects.

**Alternatives considered**: Field validator on `DocumentSection` (original spec approach) — rejected because it silently transforms stored values, making `segmentation.json` harder to debug.

---

### Decision: KV pairs removed from segmentation prompt

**Decision**: Remove `key_value_pairs` block from `_build_segmentation_prompt`. Parameter stays on `DocumentSegmenter.segment()` for call-site compatibility.

**Rationale**: User confirmed KV pairs were causing confusion in document type classification. Page summaries + filename provide sufficient context for sub-document boundary detection and type classification.

**Alternatives considered**: Keep KV pairs but filter to top-level fields only — rejected as over-engineering; page content is sufficient.

---

### Decision: `section_type` and `document_type` are independent classifiers

**Decision**: Both fields coexist on `DocumentSection`. Neither replaces the other. `section_type` = fine-grained sub-section (e.g., `material_dispensing`), `document_type` = coarse sub-document (e.g., `batch_record`). The structural relationship is defined in `document_profiles.yaml` `expected_sections`.

**Rationale**: `applicable_section_types` and `applicable_document_types` are separate dimensions in rule YAML. Merging them would break the existing filtering semantics.

---

### Decision: Unrecognized `document_type` values are preserved (not collapsed)

**Decision**: `normalize_document_type()` returns the input unchanged if it finds no alias match. The evaluator fallback chain (`(sec_info or {}).get("document_type") or document_type`) handles this — an unrecognized non-empty value passes through as `effective_doc_type`, and the applicability gate simply won't match any rules (same as an unrecognized orchestrator type).

**Rationale**: Collapsing to `""` and falling back to orchestrator type is more conservative but masks the LLM's classification attempt. Preserving lets the operator see what the LLM returned. Collapse-to-empty was the original spec approach but was superseded by the decision to see raw output.

**Alternatives considered**: Collapse unrecognized values to `""` at `build_page_to_section` level — rejected to keep raw output visible.

---

### Existing test file conflict

**Finding**: `backend/tests/compliance/test_per_section_doc_type.py` already exists and tests model-level normalization (field validator behavior). This conflicts with the final design (no field validator; normalization in `build_page_to_section`). Tests must be rewritten (Task 5).

**Impact**: The existing tests for alias resolution and collapse-to-empty will fail with the correct implementation. Task 5 is required before CI passes.
