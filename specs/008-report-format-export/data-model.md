# Data Model: Client-Aligned Compliance Report (Spec 008)

## Principle

**The on-disk JSON shape is unchanged.** New presentation types (`ReportRow`, `ReportDocument`, `ReportHeader`, `ReportFooter`) are render-time only — built by a pure transform function from the existing `ComplianceReport` payload, never persisted.

**One field is added** to `ComplianceFinding`: `mitigation_text: str = ""`. This is additive (default empty), persisted, and exists so LLM-synthesised mitigation guidance is computed once per finding and reused across re-exports.

## Existing types (no breaking change)

The following models from `backend/app/compliance/models.py` are preserved verbatim:

- `ComplianceReport`
- `AgentReport`
- `ComplianceFinding` *(additive change — new optional field, see below)*
- `RuleResult`
- `CategoryScore`
- `ExecutiveSummary`
- `ScoreMethodology`
- `SkippedAgent` / related

### Additive change to `ComplianceFinding`

```python
class ComplianceFinding(BaseModel):
    # … all existing fields preserved …
    mitigation_text: str = ""
    """LLM-synthesised mitigation guidance.

    Populated lazily on first export when the finding's
    ``recommendation`` field is empty. Persisted so subsequent
    exports reuse the cached text and don't re-incur the LLM cost.
    For findings whose ``recommendation`` is non-empty this stays
    empty — the renderer prefers ``recommendation`` directly.
    """
```

Default empty. Backward compatible: pre-feature `compliance_result.json` files load without migration.

## New render-time types (not persisted)

### `ReportRow`

```python
@dataclass(frozen=True)
class ReportRow:
    """One row of the client-aligned rule-centric report.

    Built at render time from a (RuleResult, [ComplianceFinding])
    pair. Not persisted — the source of truth is the JSON on disk.
    """

    question: str
    """The rule text shown in column 1 (verbatim ``AuditRule.text``)."""

    compliance_label: str
    """Display label: "Compliant" / "Action Required" / "Needs Attention"."""

    compliance_kind: Literal["compliant", "action_required", "needs_attention"]
    """Machine-readable kind for badge styling + telemetry counts."""

    evidence_pages: str
    """Formatted page references (e.g. "PAGE:6, 9, 31" or "PAGE:6 to 34").

    Empty string when ``compliance_kind == "compliant"`` per
    Akhilesh's directive (FR-003).
    """

    detailed_evidence: str
    """Paragraph form. For compliant rows: 2-3-sentence cross-page
    summary. For non-compliant / uncertain: the finding's reasoning
    + evidence concatenated."""

    mitigation: str
    """"Not Applicable" for compliant rows; 1-4-sentence action plan
    otherwise. Sourced from ``ComplianceFinding.recommendation``
    when present, ``ComplianceFinding.mitigation_text`` when cached,
    LLM-synthesised when neither is available."""

    agent: str
    """Agent display name (``ALCOA+`` / ``GMP`` / ``Checklist`` / ``SOP`` /
    ``Cross-Page``). Surfaces as a chip on multi-agent rule rows."""

    rule_id: str
    """For the on-screen expand drawer + HITL routing. Not displayed
    in the export."""
```

### `ReportHeader`

```python
@dataclass(frozen=True)
class ReportHeader:
    product_name: str = "BMR Compliance Intelligence Suite"
    """Masthead — the solution brand name shown centered in the
    header band. Two-layer brand model: this is the product /
    masthead name; the disclaimer footer carries the engine name
    ("Pharmix AI") separately. Default configurable via
    ``AT_COMPLIANCE__REPORT_PRODUCT_NAME``."""

    title: str
    """Report title (sits below the product name). Derived from
    ``document_type`` or agent set (e.g. "Checklist based Review",
    "ALCOA+ Compliance Report")."""

    is_draft: bool = True
    """Top-right "Document is Draft" flag — set true while HITL
    review is open; false after the doc is signed off (future)."""

    metadata_rows: list[tuple[str, str]]
    """Ordered (label, value) pairs for the metadata block:
       [("Document", "...pdf"),
        ("Product", "..."),
        ("Batch No", "..."),
        ("Date Of Validation", "YYYY-MM-DD")]
    """

    logo_path: Path | None
    """Path to a generic compliance-suite logo asset; None for
    text-only fallback when the asset is missing. Configurable
    via ``AT_COMPLIANCE__REPORT_LOGO_PATH``."""
```

### `ReportFooter`

```python
@dataclass(frozen=True)
class ReportFooter:
    operator_name: str
    """From ``X-Actor-Id`` header / scope, falls back to "System"."""

    generated_at: datetime
    """UTC timestamp; rendered in the disclaimer."""

    disclaimer_template: str = (
        "Disclaimer Note : This document is electronically generated "
        "by Pharmix AI Printed By: {operator} Printed On: {timestamp}"
    )
    """Rendered into the page footer on every page."""
```

### `ReportDocument`

```python
@dataclass(frozen=True)
class ReportDocument:
    """Complete render-time payload."""

    header: ReportHeader
    rows: list[ReportRow]
    footer: ReportFooter
```

## Transforms (pure functions)

### `build_report_document(report: ComplianceReport, *, operator: str) -> ReportDocument`

Lives in `backend/app/compliance/report_renderer/builder.py`.

Pure function: same input → same output. No I/O, no side effects, no LLM calls. The LLM-synthesis path is invoked separately during a "warm" step before this is called.

Logic:

1. Read `report.agent_reports` to enumerate per-agent `RuleResult`s.
2. For each `RuleResult` with `status != "not_applicable"`, build one `ReportRow`:
   - `question = RuleResult.rule_text`
   - `compliance_kind = _bucket_status(RuleResult.status, hitl_overrides)`
   - `compliance_label = {"compliant": "Compliant", "action_required": "Action Required", "needs_attention": "Needs Attention"}[kind]`
   - `evidence_pages = ""` if compliant else `_format_pages(RuleResult.page_numbers)`
   - `detailed_evidence = _summarise_compliant(...)` if compliant else `_concat_finding_text(findings)`
   - `mitigation = "Not Applicable"` if compliant else `_pick_mitigation(findings)`
   - `agent = AGENT_DISPLAY_NAMES[RuleResult.agent]`
3. Sort: primary by `compliance_kind` priority (action_required > needs_attention > compliant), secondary by `agent`, tertiary by `rule_id` — so the eye lands on action items first.
4. Build `ReportHeader` and `ReportFooter` from `report.filename`, `report.document_type`, `report.generated_at`, and the operator arg.

### `_bucket_status(status: str, hitl_overrides: dict) -> str`

Maps raw rule status to one of three buckets:

| Raw status | HITL override | Bucket |
|---|---|---|
| `compliant` | (any) | `compliant` |
| `non_compliant` | none / `needs_review` | `action_required` |
| `non_compliant` | `user_approved` (operator approved the finding) | `compliant` |
| `non_compliant` | `user_rejected` | `action_required` |
| `uncertain` | none / `needs_review` | `needs_attention` |
| `uncertain` | `user_approved` | `compliant` |
| `error` | (any) | `needs_attention` |
| `not_applicable` | (any) | **row excluded** |

### `_format_pages(page_nums: list[int]) -> str`

Compresses an ordered set of page integers into the reference's display syntax:

- `[103]` → `"PAGE:103"`
- `[36, 37, 38, 39, 40, 41, 42]` → `"PAGE:36 to 42"`
- `[6, 9, 31]` → `"PAGE:6, 9, 31"`
- `[6, 7, 8, 9, 10, 11, 12, 13]` → `"PAGE:6 to 13"`
- Empty list → `""`

Rule: contiguous run of ≥3 pages → `N to M`. Else comma-separated.

### `_summarise_compliant(rule_result, findings_on_other_pages) -> str`

For compliant rows. Generates a 2-3-sentence summary across all pages the rule evaluated. Two strategies, in order:

1. **Deterministic template** (preferred): if all evals on the rule have the same status="compliant" and the rule has a known "summary template" in its YAML (`AuditRule.summary_template` — future addition, not gated on this feature), interpolate page-range stats.
2. **LLM synthesis** (fallback): one LLM call per rule, persisted on `RuleResult.summary_text` (also a future additive field). For v1 of this feature, fall back to a simple deterministic template: `"Evaluated across N pages ({page_range}). All entries met the {rule_category} criteria."`.

### `_pick_mitigation(findings: list[ComplianceFinding]) -> str`

For non-compliant/uncertain rows. In order of preference:

1. The longest non-empty `recommendation` across the findings (authors put the most thought into the most-developed recommendation).
2. The first non-empty `mitigation_text` (LLM-synthesised cache).
3. LLM synthesis (one call, persisted to all findings on the rule).
4. Fallback boilerplate: `"Review and remediate. Initiate a CAPA if the gap persists."`.

## Persistence shape (unchanged on disk)

```
data/documents/{doc_id}/
├── compliance_result.json   # ComplianceReport (unchanged schema + mitigation_text field)
├── segmentation.json
├── extractions.json
├── …
└── exports/                 # (new) cached renders
    ├── report.pdf
    ├── report.html
    └── report.md
```

The `exports/` subdir is **optional** (cache only). Renderers MAY write here for fast subsequent fetches; the GET endpoint MAY read from here when the cached file is newer than `compliance_result.json`. Cache misses re-render from JSON.

## Migration strategy

- Pre-feature `compliance_result.json` files load without migration (new field defaults to `""`).
- `mitigation_text` is populated lazily on first export; existing files retroactively gain the field on first re-export.
- No on-disk migration script needed.
- No frontend type-change blocks: the `compliance.ts` types stay backward-compatible (the new `ReportRow` and friends are derived TS types in the same file).

## Out of scope for this data model

- `summary_template` field on `AuditRule` (future; rule authors will populate). For now the compliant-row summary uses the deterministic boilerplate above.
- Multi-language mitigation text. Mitigation is English-only for v1.
- Per-row `evaluated_at` timestamp (existing report-level timestamp is enough).
- HITL audit-trail per row (existing HITL fields on `ComplianceFinding` cover this).
