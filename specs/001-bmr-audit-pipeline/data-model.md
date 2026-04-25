# Phase 1 — Data Model: BMR Audit Pipeline (v2, leverage-first)

**Spec**: [spec.md](./spec.md)  **Plan**: [plan.md](./plan.md)  **Research**: [research.md](./research.md)
**Revision**: v2 (2026-04-17) — shrinks PipelineStage to 5, adds ContextObject, StructuredResolution, FeedbackSample, removes SOP stage entities.

This document defines the domain entities, their fields, validation rules, enums and state
machines, and the cross-entity invariants. Persistence is described at the port level —
adapters (Postgres / filesystem / LangGraph checkpointer) implement the ports.

---

## 1. Core Entities

### 1.1 BMRAuditRun

One execution of the BMR pipeline on one uploaded package.

Fields:
- `run_id: UUID` — PK
- `package_id: UUID` — FK to DocumentPackage (Spec 002)
- `manifest_id: str` — logical id of the manifest used (Spec 002)
- `rule_set_version: str` — content-hash + semver of the pinned rule YAML bundle
- `mode: PipelineMode` — `leverage` (default) | `process_replication`
- `mode_trigger_reason: str | null` — required if `mode = process_replication`
- `current_stage: PipelineStage`
- `status: RunStatus`
- `started_at: datetime`
- `completed_at: datetime | null`
- `started_by: UserId`
- `predecessor_run_id: UUID | null` — for re-runs or revisions
- `audit_trail_cursor: int` — monotonic sequence of the last emitted audit-trail entry

Validation:
- `mode_trigger_reason` non-null iff `mode = process_replication`
- `status` transitions governed by §2.1 state machine.
- `completed_at` non-null iff `status ∈ { COMPLETED, FAILED, CANCELLED }`.

### 1.2 PipelineStageState

One row per (run, stage). Tracks stage-level progress and sub-phase state for parallel
branches inside Compliance.

Fields:
- `run_id: UUID`
- `stage: PipelineStage`
- `status: StageStatus`
- `entered_at: datetime | null`
- `exited_at: datetime | null`
- `sub_phase: StageSubPhase | null` — only populated for `Compliance`: `{ prepared,
  alcoa_in_progress, alcoa_done, gmp_in_progress, gmp_done, joined, synthesis_in_progress,
  synthesis_done }`
- `gate_result: GateResult` — `NOT_EVALUATED | PASS | FAIL | DIRTY` (DIRTY = re-entry after
  correction)
- `error_summary: str | null`

Validation:
- Stages execute in declared order (§2.2).
- `gate_result = PASS` is required before the next stage can set its `status = IN_PROGRESS`.
- Except: the `DIRTY` state is the only way a prior stage's gate can be re-opened (via
  correction → selective re-run).

### 1.3 Finding

A single verifiable claim produced by the system.

Fields:
- `run_id: UUID`
- `logical_id: str` — stable across revisions (content-hash of `{rule_id, scope, raw_value}`)
- `revision: int` — increments on each retraction/re-emission; logical_id + revision is unique
- `capability_id: str` — the capability that produced this finding (directly or via synthesis)
- `rule_id: str`
- `rule_version: str` — pinned version of the rule from the loaded rule set
- `scope: ScopeRef` — discriminated union (see §4)
- `evidence: list[EvidenceRef]` — MUST contain ≥ 1 entry
- `alcoa_principle: ALCOAPrinciple` — enum
- `gmp_category: GMPCategory | null`
- `severity: Severity` — `CRITICAL | MAJOR | MINOR | OBSERVATION`
- `raw_value: Any`
- `expected_value: Any | null`
- `tolerance_applied: ToleranceSpec | null` — required iff numeric comparison was made
- `source: FindingSource` — `direct | synthesised`
- `source_finding_ids: list[str]` — required non-empty iff `source = synthesised`
- `hitl_state: HITLState`
- `re_run_scope: ReRunScopeRef`
- `produced_in_mode: PipelineMode`
- `contributing_factor: str | null` — e.g., `operator_proceeded_on_low_legibility`
- `created_at: datetime`
- `retracted_at: datetime | null`

Validation:
- `len(evidence) >= 1` (Constitution V).
- `source = synthesised` ⇒ `len(source_finding_ids) >= 1` AND every listed id exists in the
  same run (or prior run if revision-linked) AND `capability_id = "checklist_synthesise"`.
- `tolerance_applied` non-null iff the underlying comparison was numeric.
- A synthesised finding whose all source findings are retracted MUST be retracted in the same
  transaction (§5 invariant).

### 1.4 ContextObject

Declares how a rule's inputs are resolved at evaluation time. Stored only inside the Rule
YAML (schema owned by Spec 005), but modelled here because it drives re-run planning and
runtime evaluation.

Shape (normalised):
- `scope: "same_page" | "page_aggregate" | "cross_document"`
- For `page_aggregate`:
  - `aggregation: "sum" | "min" | "max" | "sequence" | "count"`
  - `page_selector: PageSelectorSpec` — e.g., `all_pages_of_document`, `pages_matching_regex`
- For `cross_document`:
  - `role: str` — one of the manifest's declared roles (Spec 002)
  - `entity_match: EntityMatchSpec` — `{ strategy: "step_number" | "material_name" |
    "batch_id" | "alias", normalise: bool, aliases_file?: str }`
  - `fallback: "flag_as_unevaluated" | "flag_as_indeterminate" | "treat_as_pass"`

Validation:
- Each rule's `context_object` MUST resolve at rule-load time: unknown roles, unknown
  aggregations, or missing alias files produce a hard error before the pipeline starts.
- `aliases_file` path is relative to `backend/config/rules/` and must exist.

### 1.5 StructuredResolution

A reviewer's action on a finding at the final checkpoint.

Fields:
- `resolution_id: UUID`
- `run_id: UUID`
- `finding_logical_id: str`
- `finding_revision: int`
- `action: ResolutionAction` — `CONFIRM | DISMISS | CORRECT`
- `reason_type: ResolutionReason | null` — required if `action ∈ { DISMISS, CORRECT }`
- `observed_value_on_document: str | null` — required if `action = DISMISS` and
  `reason_type ∈ { OCR_MISREAD, ACCEPTABLE_VARIANCE }`; always required if `action = CORRECT`
- `system_extracted_value: str` — snapshot of the Finding's raw_value at resolution time
  (immutable)
- `note: str | null`
- `correction_id: UUID | null` — FK to Correction if `action = CORRECT`
- `actor: UserId`
- `recorded_at: datetime` — server-assigned
- `superseded_by: UUID | null` — set when a downstream re-run invalidates this resolution

Validation:
- `action = CORRECT` ⇒ `correction_id` non-null.
- `system_extracted_value` is IMMUTABLE after insert (Constitution VIII — Original).
- `superseded_by` may only be set, never cleared.

### 1.6 Correction

A reviewer-submitted change to an extracted input.

Fields:
- `correction_id: UUID`
- `run_id: UUID`
- `resolution_id: UUID` — the StructuredResolution that authored this correction
- `target: CorrectionTarget` — `{ document_id, page_number, field_path }` or
  `{ document_id, page_number, region: EvidenceRegion }`
- `old_value: Any` — immutable snapshot
- `new_value: Any`
- `value_kind: str` — for the rerun planner (`quantity_kg`, `timestamp`, `signature_present`, …)
- `actor: UserId`
- `recorded_at: datetime`
- `plan_id: UUID` — FK to ReExecutionPlan generated from this correction

### 1.7 ReExecutionPlan

The bounded set of rule evaluations the system will perform in response to a Correction.

Fields:
- `plan_id: UUID`
- `run_id: UUID`
- `correction_id: UUID`
- `rules_to_reeval: list[{ rule_id, rule_version, scope: ScopeRef }]`
- `capabilities_to_invoke: list[{ capability_id, scope: ScopeRef }]`
- `estimated_runtime_seconds: int`
- `status: PlanStatus` — `PROPOSED | CONFIRMED | EXECUTING | COMPLETED | CANCELLED`
- `confirmed_by: UserId | null`
- `confirmed_at: datetime | null`
- `created_at: datetime`

Validation:
- Every entry in `rules_to_reeval` MUST be reachable from the Correction target via the rule
  engine's `context_object` reverse dependency graph.
- A plan with empty `rules_to_reeval` is still persisted (so the Correction is auditable) but
  its status jumps directly to `COMPLETED` with a note.

### 1.8 FeedbackSample

An accumulable training-signal record derived from a StructuredResolution.

Fields:
- `sample_id: UUID`
- `run_id: UUID`
- `rule_id: str`
- `rule_version: str`
- `resolution_action: ResolutionAction`
- `reason_type: ResolutionReason | null`
- `input_context_digest: str` — content hash of the rule's resolved context_object inputs
- `observed_vs_extracted: { observed: str, extracted: str } | null`
- `raw_finding_snapshot: JSON` — immutable snapshot of the Finding at resolution time
- `recorded_at: datetime`

Validation:
- One row per StructuredResolution; insert is idempotent on `(resolution_id)`.
- `raw_finding_snapshot` is immutable and includes evidence refs to allow later OCR
  fine-tuning to pull the source pixels.

### 1.9 HITLAction

Unified record of every reviewer action at any HITL touchpoint.

Fields:
- `action_id: UUID`
- `run_id: UUID`
- `touchpoint: HITLTouchpoint` — `LEGIBILITY_GATE | FINAL_CHECKPOINT`
- `scope: ScopeRef`
- `kind: HITLActionKind` — depends on touchpoint:
  - For `LEGIBILITY_GATE`: `PAGE_REUPLOADED | PROCEED_ANYWAY`
  - For `FINAL_CHECKPOINT`: `FINDING_CONFIRMED | FINDING_DISMISSED | FINDING_CORRECTED`
- `actor: UserId`
- `recorded_at: datetime`
- `payload_ref: UUID | null` — FK to StructuredResolution, Correction, or
  PageReuploadEvent depending on kind

Validation:
- `LEGIBILITY_GATE` touchpoint MUST NOT emit `FINDING_*` actions (Constitution IV).
- `FINAL_CHECKPOINT` touchpoint MUST NOT emit `PAGE_*` actions.

### 1.10 AuditTrailEntry

Append-only event log. Every state-changing operation writes exactly one entry.

Fields:
- `entry_id: UUID` (monotonic in-run via `run_id, seq_no`)
- `run_id: UUID`
- `seq_no: int` — monotonic per run
- `event_type: str` — controlled vocabulary (see Event Contract §6)
- `actor: UserId | "system"`
- `recorded_at: datetime` — server-assigned
- `payload: JSON` — event-type-specific schema-versioned payload
- `schema_version: str`

Validation:
- Immutable once inserted (Constitution VIII — Enduring).
- `(run_id, seq_no)` is unique and gap-free.

---

## 2. Enumerations & State Machines

### 2.1 RunStatus

`CREATED → STARTED → IN_PROGRESS → AWAITING_LEGIBILITY_HITL → IN_PROGRESS → AWAITING_FINAL_CHECKPOINT → IN_PROGRESS (re-run) → COMPLETED`

Terminal states: `COMPLETED`, `CANCELLED`, `FAILED`. From any non-terminal state a run MAY
enter `CANCELLED` (operator cancel) or `FAILED` (unrecoverable error).

### 2.2 PipelineStage

`INGEST → LEGIBILITY_AND_CLASSIFICATION → STRUCTURED_EXTRACTION_AND_SUMMARISATION → COMPLIANCE → REPORT_AND_RESOLUTION`

(Dropped from v1 data-model: `CLASSIFY`, `QUALITY_GATE`, `STEP_WALK`, `AGGREGATE`,
`REPORT` — collapsed into the 5 above. See Migration §7.)

### 2.3 StageStatus

`NOT_STARTED → IN_PROGRESS → { PASSED | FAILED | DIRTY }`
`DIRTY → IN_PROGRESS → { PASSED | FAILED }` on re-execution.

### 2.4 ResolutionAction

`CONFIRM | DISMISS | CORRECT`

### 2.5 ResolutionReason

`OCR_MISREAD | ACCEPTABLE_VARIANCE | DUPLICATE_FINDING | OUT_OF_SCOPE | RULE_MISCONFIGURED | OTHER`

### 2.6 FindingSource

`direct | synthesised`

### 2.7 PipelineMode

`leverage | process_replication`

### 2.8 HITLState

`PENDING | CONFIRMED | DISMISSED | CORRECTED | STALE_FROM_RERUN`

---

## 3. Scope / Evidence Reference Types

### 3.1 ScopeRef (discriminated union)

- `{ kind: "document", document_id }`
- `{ kind: "page", document_id, page_number }`
- `{ kind: "bpcr_step", document_id, step_number }`
- `{ kind: "entity_match", document_ids: [UUID], match_key: str }` — for cross-doc rules

### 3.2 EvidenceRef

- `{ document_id, page_number, region?: EvidenceRegion }`

### 3.3 EvidenceRegion (discriminated union)

- `{ kind: "bounding_box", box: [x, y, w, h], units: "normalised" | "pixels" }`
- `{ kind: "text_span", start: int, end: int, source: "ocr_text" | "pdf_text" }`

### 3.4 ReRunScopeRef

- `{ kind: "document", document_id }`
- `{ kind: "page", document_id, page_number }`
- `{ kind: "bpcr_step", document_id, step_number }`
- `{ kind: "capability", capability_id, scope: ScopeRef }`

---

## 4. ToleranceSpec

- `{ kind: "absolute", value: float, unit: str }`
- `{ kind: "percent", value: float }`
- `{ kind: "range", min: float, max: float, unit: str }`
- `{ kind: "exact", normalise: "whitespace_only" | "case_insensitive" | "none" }` — for
  string comparisons

---

## 5. Cross-Entity Invariants

5.1. Every `Finding` row references an existing `rule_id + rule_version` in the run's pinned
rule set. Rules cannot change mid-run.

5.2. A `Correction` cannot be created without a preceding `StructuredResolution` whose
`action = CORRECT`.

5.3. A `ReExecutionPlan` must be `CONFIRMED` before its rules are re-evaluated. The
`EXECUTING` transition requires a `confirmed_by`.

5.4. When a `Correction` retracts a Finding whose prior resolution was `CONFIRM` or `DISMISS`,
the prior `StructuredResolution.superseded_by` MUST be set and the finding's `hitl_state`
transitions to `STALE_FROM_RERUN`.

5.5. When any source finding of a synthesised finding is retracted, the synthesised finding
MUST be either retracted or re-synthesised in the same transaction (§1.3 validation).

5.6. Raw extracted values in `Finding.raw_value` and `Correction.old_value` are IMMUTABLE.
Any change produces a new Finding revision or a new Correction — never an in-place mutation.

5.7. `recorded_at` timestamps are assigned server-side at insert. Client-supplied timestamps
are ignored (Constitution VIII — Contemporaneous).

5.8. Every write to `Finding`, `StructuredResolution`, `Correction`, `ReExecutionPlan`,
`HITLAction` MUST produce exactly one `AuditTrailEntry` in the same transaction.

5.9. `FeedbackSample` is inserted exactly once per `StructuredResolution`, idempotent on
resolution_id. Inserts never update existing rows.

---

## 6. Persistence Layout

- **LangGraph checkpointer (Postgres)**: `BMRAuditRun`, `PipelineStageState`, and
  pipeline-internal working state. Checkpoint granularity: stage boundary + parallel-branch
  join points.
- **Postgres domain tables**:
  - `bmr_findings` — `Finding` rows
  - `bmr_resolutions` — `StructuredResolution` rows
  - `bmr_corrections` — `Correction` rows
  - `bmr_rerun_plans` — `ReExecutionPlan` rows
  - `bmr_hitl_actions` — `HITLAction` rows
  - `bmr_audit_trail` — `AuditTrailEntry` rows (partition by `run_id`)
  - `bmr_feedback_samples` — `FeedbackSample` rows (retention policy per data-retention spec)
- **Filesystem JSON (document-of-record)**: the audit artifact bundle (findings + resolutions
  + exported PDF) is snapshotted to disk at run completion; this snapshot is the regulatory
  record. Postgres is orchestration state only (Architectural Constraints).

Port surface (core/ports):
- `BMRRunStore`, `FindingStore` (extends existing), `ResolutionStore`, `CorrectionStore`,
  `ReExecutionPlanStore`, `HITLActionStore`, `AuditTrailStore`, `FeedbackCorpusStore`.

---

## 7. Migration from v1 (7-stage) Model

| v1 entity/enum | v2 treatment |
|---|---|
| `PipelineStage.CLASSIFY` | Absorbed into `LEGIBILITY_AND_CLASSIFICATION` |
| `PipelineStage.QUALITY_GATE` | Absorbed into `LEGIBILITY_AND_CLASSIFICATION` gate |
| `PipelineStage.STEP_WALK` | Absorbed into `COMPLIANCE` (via `cross_doc_rule_eval` + rule context_object) |
| `PipelineStage.AGGREGATE` | Absorbed into `COMPLIANCE` / `REPORT_AND_RESOLUTION` |
| Old `HITLAction` / `FindingAction` dual naming | Unified under `HITLAction` with `touchpoint` + `kind` |
| SOP entities / agents | REMOVED at runtime; SOP rules live in ALCOA/GMP banks (Spec 005) |
| Separate reconciliation engine entities (Spec 003 v1) | REMOVED; subsumed by `ContextObject` |

No production data exists yet; v1 was draft-only. No backfill needed.

---

## 8. Open Questions (tracked, not blocking)

- Retention window for `FeedbackSample` rows — defer to data-retention spec; default 2 years.
- Exact `value_kind` vocabulary for `Correction.value_kind` — seeded from pilot rule set;
  grow as needed.
- Whether `rule_version` uses content-hash, semver, or both — chosen at rule-loader
  implementation; spec 005 decides.
