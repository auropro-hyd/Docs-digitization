# Data Model: BMR Audit Report & Finding-Level HITL

**Feature**: 004 | **Spec Version**: v2

`StructuredResolution` and `FeedbackSample` are defined in Spec 001; this spec extends
their usage and adds four new entities: `AuditReportRevision`, `ReportSection`,
`CorrectionWorkflow`, and `EvidenceLink` (projection view-model).

## 1. Enumerations

### 1.1 `ResolutionAction`
`CONFIRM` | `DISMISS` | `CORRECT`

### 1.2 `DismissReasonType` (YAML-extensible seed)
`OCR_MISREAD` | `ACCEPTABLE_VARIANCE` | `DUPLICATE_FINDING` | `OUT_OF_SCOPE` |
`RULE_MISCONFIGURED` | `OTHER`

### 1.3 `ExportGateStatus`
`ready` | `blocked_by_pending_findings` | `blocked_by_stale_resolutions`

### 1.4 `CorrectionStatus`
`drafted` | `preview_computed` | `confirmed` | `rerun_in_progress` | `rerun_complete` |
`reverted`

## 2. New Entities

### 2.1 `AuditReportRevision` (append-only)
| Field | Type | Notes |
|---|---|---|
| `id` | ULID | PK |
| `run_id` | FK → Spec 001 `Run` | |
| `revision_number` | int | monotonic per run |
| `predecessor_id` | FK → self, nullable | |
| `pdf_sha256` | string | content-addressed export blob |
| `bundle_sha256` | string | content-addressed JSON bundle |
| `exported_by` | string | actor id |
| `exported_at` | timestamp | |
| `sections_manifest_id` | string | `report-sections.yaml` version |
| `severity_gating_id` | string | `report-severity-gating.yaml` version |
| `findings_snapshot` | JSONB | immutable list of finding ids + actions at export time |

### 2.2 `CorrectionWorkflow` (append-only)
| Field | Type | Notes |
|---|---|---|
| `id` | ULID | PK |
| `run_id` | FK | |
| `finding_id` | FK → `Finding` | the finding the reviewer corrected from |
| `document_ref_id` | FK | target of the correction |
| `field_name` | string | |
| `from_value` | string (JSON-encoded) | immutable snapshot |
| `to_value` | string (JSON-encoded) | |
| `reason_type` | enum: `ocr_misread` / `mis_extraction` / `genuine_scrivener_error` / `other` | |
| `reason_comment` | text, optional | |
| `rerun_plan_snapshot` | JSONB: `{rule_ids: [...], estimated_scope: "..."}` | captured at preview |
| `status` | `CorrectionStatus` | |
| `invalidated_finding_ids` | JSONB: `[string]` | nullable until rerun_complete |
| `new_finding_ids` | JSONB: `[string]` | nullable until rerun_complete |
| `actor_id` | string | |
| `created_at` | timestamp | |
| `completed_at` | timestamp, nullable | |

### 2.3 `ReportSection` (projection view-model; not persisted per run — computed)
| Field | Type | Notes |
|---|---|---|
| `id` | string | e.g. `step-03` or `doc-bpcr-summary` |
| `group_kind` | enum: `bpcr_step` / `document_scope` | |
| `group_ref` | JSONB: `{step_number?: int, document_ref_id?: string}` | |
| `sub_sections` | JSONB: `[{kind: "alcoa"|"gmp"|"checklist", finding_ids: [...]}]` | |
| `severity_counts` | JSONB: `{critical, major, minor, info}` | per group |
| `all_actioned` | bool | true iff every blocking-severity finding has an active resolution |

### 2.4 `EvidenceLink` (projection view-model; server-computed per request)
| Field | Type | Notes |
|---|---|---|
| `finding_id` | string | |
| `sources` | JSONB: `[{document_ref_id, page_number, region?: {x,y,w,h} | {span_start, span_end}, label?: string}]` | |
| `has_no_specific_region` | bool | true for page-level-only findings |

## 3. Extensions to Spec 001 entities

### 3.1 `StructuredResolution` (reused; clarified use in this spec)

- `action` ∈ `ResolutionAction` (1.1)
- `reason_type` required when `action = DISMISS` or `action = CORRECT`.
  - `CONFIRM` ⇒ `reason_type = null`, `observed_value_on_document = null`.
  - `DISMISS` with `reason_type ∈ {OCR_MISREAD, ACCEPTABLE_VARIANCE}` ⇒
    `observed_value_on_document` REQUIRED.
  - `DISMISS` with other `reason_type` ⇒ `observed_value_on_document` OPTIONAL.
  - `CORRECT` ⇒ handled via `CorrectionWorkflow` (2.2); the resolution row links to the
    workflow via `correction_workflow_id`.
- `system_extracted_value` snapshot from finding at resolution time, immutable.
- `supersedes_id` populated when re-run invalidates the prior resolution; the superseded
  row is not deleted, it's marked `needs_re_action=true`.

### 3.2 `FeedbackSample` (reused; seeded by this spec)

Created synchronously on every `StructuredResolution` persist via `feedback_seed.v1`. The
sample carries an immutable finding + resolution snapshot and the inputs digest.

## 4. Cross-Entity Validation Rules

1. Export is permitted iff `report_section.all_actioned` is true for every section whose
   findings include at least one blocking-severity finding (per `report-severity-gating.yaml`).
2. A `CorrectionWorkflow` MUST NOT transition `status=confirmed → rerun_complete` without a
   rerun-plan snapshot.
3. Every `StructuredResolution` with `action ∈ {DISMISS, CORRECT}` MUST have a
   `FeedbackSample` created within the same transaction; absence is a bug.
4. `AuditReportRevision.revision_number` is strictly monotonic per `run_id`; predecessor
   chain MUST form a single-parent DAG (no branches).
5. `AuditReportRevision.findings_snapshot` MUST reference resolution ids whose
   `superseded_by` is null at the time of export.
6. `CorrectionWorkflow.invalidated_finding_ids` MUST be a subset of findings whose rules
   appeared in `rerun_plan_snapshot.rule_ids`.
7. `EvidenceLink.sources` MUST be non-empty for every finding EXCEPT those marked
   `has_no_specific_region=true`.
8. No `ReportSection` row may mix findings from different runs.

## 5. Persistence & projection strategy

- `audit_report_revision`, `correction_workflow` are persisted tables.
- `ReportSection` and `EvidenceLink` are server-side projections computed per request from
  `finding`, `structured_resolution`, `resolved_context` (Spec 003) — cached with TTL 30 s
  per run.
- Report bundle JSON schema is versioned; Spec 004 owns the schema file
  `contracts/bundle.schema.json` (referenced by `exporter_bundle.py`).
