# Data Model: Robust Segmentation Coverage

**Feature**: 011-segmentation-robust-coverage
**Created**: 2026-05-14

This spec is additive — no existing schema breaks. The new entities live in `app.compliance.segmentation` and `app.compliance.models`.

## New types

### `PageHeader`

```python
@dataclass(frozen=True)
class PageHeader:
    """One ``Page X of Y`` parse result from a single page's OCR markdown."""

    page_num: int          # absolute page number in the packet (1-indexed)
    x: int                 # the X in "Page X of Y" — within-document index
    y: int                 # the Y in "Page X of Y" — total pages in that sub-document
    raw: str               # the matched text (for telemetry / HITL display)
    confidence: float      # 1.0 for "Page X of Y"; 0.7 for tolerated typos
```

Returned by `parse_page_headers(extractions: list[dict]) -> list[PageHeader]`. Pages without a detectable header don't appear.

### `BoundaryUnit`

```python
@dataclass(frozen=True)
class BoundaryUnit:
    """A contiguous run of pages whose headers attest they belong to the same
    logical sub-document."""

    start_page: int        # first page in the unit
    end_page: int          # last page in the unit
    expected_pages: int    # the ``Y`` value all pages share
    header_count: int      # how many pages in the run actually carried a parseable header
```

Returned by `group_boundary_units(headers: list[PageHeader]) -> list[BoundaryUnit]`. The `end_page - start_page + 1` may exceed `expected_pages` if a typo'd header introduced ambiguity; in that case `header_count` < `expected_pages` and a `header_low_confidence` event fires.

### `SegmentationOverride`

```python
class SegmentationOverride(BaseModel):
    """One operator edit to a specific field of a specific section."""

    section_id: str
    field: Literal["section_type", "document_type", "start_page", "end_page", "name"]
    value: str | int                    # str for type/name fields; int for page fields
    recorded_at: datetime
    actor: str                          # operator identity from the HITL request
```

Stored as a JSON list at `data/documents/{doc_id}/segmentation.overrides.json`. One file per doc. Order is insertion-order; later overrides on the same `(section_id, field)` shadow earlier ones (we keep history for audit but the last one wins on apply).

### Extension of `SegmentationIssue.kind` (existing model)

The existing `SegmentationIssue` dataclass at [app/compliance/segmentation.py:69-77](backend/app/compliance/segmentation.py#L69-L77) carries a free-form `kind: str` field. This spec extends the convention with new kind values; no schema change:

| New `kind` | Source FR | Emitted when |
|---|---|---|
| `header_boundary_merged` | FR-005 | Two LLM sections within one boundary unit got merged |
| `header_boundary_split` | FR-005 | One LLM section spanning two boundary units got split |
| `header_low_confidence` | FR-001 | Tolerated-typo header parse, confidence < 1.0 |
| `boundary_conflict` | edge case | Two pages claim different `Y` values (e.g. `1 of 3` then `1 of 5`) |
| `output_truncated` | FR-006 | Coverage shortfall + finish_reason=length |
| `retry_exhausted` | FR-008 | Retry chain failed; remaining range filled as `unknown` |
| `missing_required_section` | FR-009 | Profile lists a `required: true` section absent from the segmentation |
| `no_kv_evidence` | FR-010 | Section spans ≥3 pages with zero KV pairs in range |
| `type_mismatch` | FR-011 | section_type not in document_type's profile |
| `override_orphaned` | FR-013 | Stored override's target section is missing from the new LLM output |

### Extension of `DocumentSegmentation` (existing Pydantic model)

Add one optional field to surface validators to the HITL response (FR-014):

```python
class DocumentSegmentation(BaseModel):
    # ... existing fields stay ...

    validation_issues: list[SegmentationIssueDict] = Field(default_factory=list)
    """Quality issues found by ``validate_segmentation`` post-process. One
    entry per issue. Empty when the segmentation is clean. Carried so the
    HITL editor can render them without a second endpoint call."""
```

`SegmentationIssueDict` is a JSON-serialisable view of `SegmentationIssue`:

```python
class SegmentationIssueDict(TypedDict):
    kind: str
    message: str
    section_ids: list[str]
    page_range: list[int] | None    # [start, end] or None
```

## Behaviour-change summary

The post-process pipeline order inside `DocumentSegmenter.segment()` becomes:

```
LLM call
  ↓
clamp_page_ranges          # existing (PR #69)
  ↓
apply_overrides            # NEW (FR-013) — sidecar file applied if present
  ↓
resolve_overlaps           # existing (PR #69)
  ↓
detect_truncation + retry  # NEW (FR-006, FR-007, FR-008)
  ↓
fill_gaps_with_unknown     # existing (PR #45)
  ↓
merge_split_by_boundary    # NEW (FR-003, FR-004) — uses parsed page headers
  ↓
normalize_section_types_to_canonical  # existing (PR #69)
  ↓
stamp_document_types       # existing
  ↓
validate_segmentation      # existing — now emits the new kinds (FR-009 to FR-011)
  ↓
attach validation_issues   # NEW (FR-014)
```

Existing callers receive the same `DocumentSegmentation` shape; the new `validation_issues` field defaults to an empty list, so consumers that don't read it stay correct.

## Storage: `segmentation.overrides.json`

```json
[
  {
    "section_id": "raw_material_request_and_issue",
    "field": "end_page",
    "value": 47,
    "recorded_at": "2026-05-14T12:34:00Z",
    "actor": "anmol@auropro.com"
  }
]
```

- One file per `doc_id`, located alongside `segmentation.json`.
- Appended on every `PUT /api/compliance/{doc_id}/segmentation`; we keep history. On apply, the last record per `(section_id, field)` wins.
- Read on every `POST /api/compliance/{doc_id}/segment` after the LLM call returns.
- Deleted only via an explicit operator action — never auto-cleaned.

## Wire-shape: `GET /api/compliance/{doc_id}/segmentation` response

The existing endpoint already returns the full `DocumentSegmentation` JSON. With FR-014 it gains a `validation_issues` array — strictly additive. Sample:

```json
{
  "sections": [...],
  "document_type": "batch_record",
  "confidence": 0.91,
  "validation_issues": [
    {
      "kind": "header_boundary_merged",
      "message": "Sections 'rm_req_pt1' (30-31) and 'rm_iss_pt2' (32) merged: pages share Page X of 3 header",
      "section_ids": ["rm_req_pt1", "rm_iss_pt2"],
      "page_range": [30, 32]
    },
    {
      "kind": "missing_required_section",
      "message": "batch_record profile requires 'cover_page' but no section emitted it",
      "section_ids": [],
      "page_range": null
    }
  ]
}
```

## What does NOT change

- `compliance_result.json` schema — unchanged.
- The HITL review state on findings — unchanged.
- The `ComplianceReport` model and the export renderers — unchanged.
- The frontend segmentation editor — accepts the existing shape; the new `validation_issues` field is opt-in to render.
- The mitigation synth endpoint and rule-table endpoint from Spec 008 — unchanged.
