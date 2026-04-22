# Capability Contracts (Spec 002 additions)

**Feature**: 002 | **Version**: v1

These capabilities are atomic per Constitution III, implemented under `app/capabilities/`,
and conform to the base `Capability` ABC defined in Spec 001
(`specs/001-bmr-audit-pipeline/contracts/capability-contract.md`).

## `boundary_detect.v1`

**Purpose**: Resolve the logical document boundaries in an uploaded physical PDF. Returns a
list of page ranges with an optional role hint per range.

**Inputs**:
- `physical_file_id: str`
- `page_count: int`
- `ocr_text_by_page: map[int, str]` (first/last band only OK)
- `config: { page_header_regex: string, cluster_similarity_min: float, classifier_confidence_min: float }`

**Outputs**:
- `ranges: [{ start: int, end: int, method: BoundaryDetectionMethod, role_hint?: string, confidence: float }]`
- `findings: []` (no findings directly; ambiguous cases surface via `method=content_classification` + low confidence)

**Invariants**:
- `ranges` MUST cover `[1, page_count]` with no gaps and no overlaps.
- A single-doc file emits exactly one range `(1, page_count, method=page_header|reviewer_override)`.

## `page_summary.v1`

**Purpose**: Generate a structured page-level summary using a `SummaryTemplate` whose scope
is `page`.

**Inputs**:
- `document_ref_id: str`
- `page_number: int`
- `page_text: str` (post-OCR)
- `page_image_ref: str` (for VLM-backed templates)
- `template: SummaryTemplate`

**Outputs**:
- `summary: { content: object, confidence: float, evidence: [{page, region?}] }`

**Invariants**:
- `content` keys MUST be a superset of `template.required_fields`.
- If a required field cannot be extracted, `content[field] = null` with a per-field
  `confidence` entry; capability MUST NOT raise.

## `doc_summary.v1`

**Purpose**: Generate a structured document-level summary using a `SummaryTemplate` whose
scope is `document`.

**Inputs**:
- `document_ref_id: str`
- `document_text: str` (post-OCR, concatenated with page markers)
- `template: SummaryTemplate`

**Outputs**:
- `summary: { content: object, confidence: float, evidence: [{page, region?}] }`

**Invariants**:
- Same `required_fields` rule as `page_summary.v1`.
- MUST emit one evidence ref per populated field (traceability).

## Cross-capability rules

- These three capabilities are side-effect free; the stage orchestrator persists their
  outputs as `BoundaryOverride`, `Summary` rows via the appropriate stores.
- All three take a `CapabilityContext` (from Spec 001) carrying `run_id`, `actor`, and a
  logger. Logs MUST include the capability id + version for audit-trail reproducibility.
