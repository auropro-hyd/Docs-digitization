# Feature Specification: BMR Audit Pipeline

**Feature Branch**: `001-bmr-audit-pipeline`
**Created**: 2026-04-17
**Last Revised**: 2026-04-17 (v2 — reframed from 7-stage process-replication to 5-stage leverage-first, per Constitution v1.1.0)
**Status**: Draft
**Input**: User description: "BMR audit pipeline that leverages the existing compliance framework (ALCOA / GMP / rule-engine / OCR / VLM) and adds multi-document orchestration, legibility pre-check, configurable summaries, page-level aggregation, cross-document rule evaluation, checklist synthesis, and a consolidated findings UI with structured resolution and selective re-run."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — QA reviewer runs an end-to-end BMR audit from upload to report (Priority: P1)

A QA reviewer receives a completed BMR package for a batch and needs the system to produce a defensible audit report that flags data-integrity issues across all documents in the package. They upload the package, the pipeline runs ingest → legibility & classification → structured extraction & summarisation → compliance (ALCOA and GMP in parallel, then Checklist-Synthesis) → report. They review findings in a single consolidated view with collapsible ALCOA / GMP / Checklist sections, resolve each with a structured comment (reason type + observed/expected values), correct where needed (triggering selective re-run), and export.

**Why this priority**: This is the single end-to-end flow the product delivers. Without it, every other feature is partial. It is also the demo flow for the pilot client.

**Independent Test**: Upload a known-good pilot BMR package, let the pipeline complete, compare emitted findings to a gold-standard auditor markup, and confirm the system-flagged issues match or exceed the human baseline.

**Acceptance Scenarios**:

1. **Given** a complete BMR package (BPCR + raw-material pages + checklists + analysis report), **When** the reviewer starts an audit, **Then** the system executes Ingest → Legibility&Classification → StructuredExtraction&Summarisation → Compliance (ALCOA∥GMP, then Checklist-Synthesis) → Report, and surfaces per-stage progress with per-document and per-page status.
2. **Given** the pipeline reaches the end, **When** the reviewer opens the consolidated findings view, **Then** they see all findings grouped by severity and BPCR step, each with document/page/region evidence, ALCOA+ principle, GMP category where applicable, `rule_id`, and raw-vs-expected values, presented inside collapsible ALCOA / GMP / Checklist-Adherence sections.
3. **Given** the reviewer resolves each finding (Confirm / Dismiss with structured comment / Correct), **When** all Critical and Major findings have been actioned, **Then** the reviewer can export the audit report, and the export is blocked until then.
4. **Given** the reviewer corrects a single extracted value, **When** they confirm the correction, **Then** the system recomputes only the rules whose `context_object` reads that value (directly or transitively), updates the relevant findings, and preserves all other resolved actions.

---

### User Story 2 — Reviewer handles legibility exceptions at the page level (Priority: P1)

While extraction is running, some pages fail a light legibility pre-check (low scan resolution, handwriting too unclear, torn corner). The reviewer must decide per page: re-upload a better scan, or proceed anyway. They must NOT be forced to resolve full compliance findings at this stage.

**Why this priority**: Without legibility gating, downstream compliance findings on unreadable inputs are noise. But making the gate heavy (forcing full finding-level review mid-pipeline) fragments reviewer attention and violates the single-final-checkpoint principle.

**Independent Test**: Inject 1–2 low-legibility pages into the pilot package. Verify the Legibility stage flags exactly those pages, presents only {re-upload, proceed} options (no compliance-finding UI), and resumes the pipeline for all non-flagged pages without blocking.

**Acceptance Scenarios**:

1. **Given** ingest completed, **When** Legibility & Classification runs, **Then** each page receives a legibility verdict with a confidence score and (for low-confidence pages) a flagged reason (scan resolution, handwriting clarity, missing header, etc.).
2. **Given** pages flagged for human review, **When** the reviewer views the Legibility queue, **Then** the only actions presented are "re-upload this page" (with upload control) and "proceed anyway" (with optional note). Finding-level review is not offered here.
3. **Given** the reviewer uploads a replacement page, **When** it is accepted, **Then** the pipeline re-runs legibility for that page only; downstream work for other pages continues uninterrupted.
4. **Given** the reviewer chooses "proceed anyway", **When** downstream compliance detects issues attributable to poor legibility, **Then** those findings are tagged `contributing_factor: operator_proceeded_on_low_legibility`.

---

### User Story 3 — Reviewer corrects a single extracted value and the system recomputes just the affected rules (Priority: P1)

At the final checkpoint, the reviewer notices a value in the report was mis-extracted by OCR: the raw-material page shows `12.5 kg` but the system extracted `12.4 kg`, producing a spurious quantity-mismatch finding. They submit the correction. The system identifies which rules had a `context_object` matching that input, re-evaluates only those rules, retracts the spurious finding, leaves other resolved actions intact, and reports the re-run scope before starting.

**Why this priority**: Corrections are the common case at QA review. If each correction triggers a full re-run, the workflow is unusable. The product's value hinges on a tight correction loop.

**Independent Test**: Plant a known OCR misread in a fixture. Verify the correction triggers re-evaluation of only the rules whose `context_object` touches that input (measurable: a logged plan showing ≤ N rules), and that unrelated resolved actions are preserved.

**Acceptance Scenarios**:

1. **Given** the reviewer submits a value correction on a specific page/field, **When** the system computes the re-run plan, **Then** the UI shows "`K` rules will be re-evaluated at scopes `[...]`" before confirming execution.
2. **Given** the reviewer confirms the plan, **When** re-execution completes, **Then** findings that depended on the corrected input are either retracted or updated, and all other findings retain their prior `hitl_state`.
3. **Given** a re-run alters a finding whose reviewer had already confirmed it, **When** the finding is updated, **Then** the reviewer is notified of the retraction/change and prompted to re-action it.
4. **Given** the re-run completes, **When** the reviewer inspects the audit trail, **Then** each affected rule evaluation shows its input digest, the correction that triggered re-evaluation, and the new outcome.

---

### User Story 4 — Checklist findings are synthesised from ALCOA / GMP outputs where possible (Priority: P2)

Many checklist items ("Raw material requisition raised?", "Weighing entries present?", "Signatures on each step?") are already implicitly validated by ALCOA / GMP rules. The reviewer should not see two separate findings for the same underlying evidence. The Checklist-Synthesis capability should derive checklist verdicts from ALCOA / GMP findings where possible, and fall back to direct rule evaluation (against OCR / summaries) for checklist items that cannot be synthesised.

**Why this priority**: Duplicated findings burden the reviewer and fracture the evidence trail. Synthesis makes the checklist layer cheap and consistent with what ALCOA/GMP already concluded.

**Independent Test**: Construct fixtures where (a) an ALCOA/GMP rule already establishes a checklist item's answer, and (b) a checklist item has no ALCOA/GMP counterpart. Verify (a) produces a single synthesised finding citing the source finding(s), and (b) produces a fallback-evaluated finding.

**Acceptance Scenarios**:

1. **Given** the Compliance stage emits ALCOA/GMP findings, **When** Checklist-Synthesis runs, **Then** each checklist rule tagged `synthesises_from: [...]` resolves by inspecting the listed `rule_id` findings and produces a synthesised finding whose `source_finding_ids` are populated.
2. **Given** a checklist rule has no synthesis source, **When** Checklist-Synthesis runs, **Then** the rule is evaluated directly via its declared capability + `context_object` (OCR extraction or document summary), and the resulting finding is marked `source: direct`.
3. **Given** a source ALCOA/GMP finding is later retracted by correction, **When** the synthesised finding depends on it, **Then** the synthesised finding is retracted or re-synthesised as appropriate.

---

### User Story 5 — Reviewer sees one consolidated findings page with collapsible compliance sections (Priority: P2)

Rather than separate ALCOA / GMP / SOP / Checklist finding lists, the reviewer sees a single page grouped by BPCR step (or document scope where no step applies), with collapsible sections per compliance family. Resolutions are inline and require a structured comment.

**Why this priority**: Four parallel finding lists forced the reviewer to context-switch and re-link evidence manually. A single grouped view matches how auditors actually read a BMR.

**Independent Test**: Generate a run with findings spanning all compliance families. Verify the UI groups them by step first, then exposes collapsible sections per family, and the resolution control enforces the structured comment schema.

**Acceptance Scenarios**:

1. **Given** all findings are produced, **When** the reviewer opens the report, **Then** findings group by BPCR step (or document scope), with collapsible ALCOA / GMP / Checklist-Adherence sub-sections.
2. **Given** the reviewer resolves a finding, **When** they pick `Dismiss` or `Correct`, **Then** a structured comment is required: `reason_type` from a controlled list, `observed_value_on_document`, `system_extracted_value`, and optional `note`.
3. **Given** a resolution is submitted, **When** the system persists it, **Then** the resolution is stored as a structured record (not a free-text blob) and becomes part of the feedback corpus (for rule-spec tuning and OCR fine-tuning — detailed in spec 004 + spec 005).

---

### User Story 6 — Operator opts into degraded-mode (process-replication) fallback when leverage produces poor findings (Priority: P3)

After running the leverage-first pipeline on a problematic package, the operator sees an unmanageable volume of findings (e.g., > 100 or systematic false positives not expressible as a rule). They explicitly switch the run to degraded-mode, which executes a sequential step-by-step replication aligned to the BPCR step order and re-produces the findings under that model for comparison.

**Why this priority**: Operationally necessary escape hatch, not the demo path. Built only if leverage-mode evaluation shows specific package types that cannot be handled declaratively.

**Independent Test**: Toggle degraded-mode on a package. Verify it executes a different internal orchestration, records its mode selection and trigger reason in the run's audit trail, and produces findings in the same data model as leverage-mode.

**Acceptance Scenarios**:

1. **Given** a run in leverage-mode, **When** the operator opts into degraded-mode, **Then** the run's audit trail records `mode_switched: process_replication` with `trigger_reason` and `actor`.
2. **Given** the run completes in degraded-mode, **When** findings are emitted, **Then** each finding is tagged with `produced_in_mode: process_replication`, and the comparison view vs. leverage-mode findings is available to the operator (for later rule-spec improvement).

---

### Edge Cases

- **Package with a non-BPCR anchor document**: If the uploaded package has no recognisable BPCR (boundary detection and content classification both fail), the pipeline stops after Legibility & Classification and produces a single error finding explaining the missing anchor; no compliance stage runs.
- **Multiple BPCR candidates** (duplicate uploads): If classification identifies more than one BPCR, the reviewer is asked to pick which one is authoritative before Compliance runs.
- **Rule references a capability not in the registry**: Pipeline fails fast at rule-load time with a clear error pointing at the rule and capability; no partial run is recorded.
- **Rule references a `context_object` that cannot be resolved** (required cross-doc role missing): Rule's declared `fallback` (`flag_as_unevaluated` / `treat_as_pass` / `flag_as_indeterminate`) is applied, and an `UNEVALUATED_CONTEXT_MISSING` finding is emitted with pointer to the missing role.
- **Re-run plan is empty** (correction affects no rule): The UI reports "No findings depend on this value; no re-evaluation needed" and persists the correction to the immutable correction log for traceability.
- **Checklist synthesis source was retracted**: The synthesised finding retracts or re-synthesises. No stale synthesised findings reach the report.
- **Correction invalidates a resolved action**: System notifies the reviewer and surfaces the affected action for re-review; the prior resolution is retained in the audit trail with a `superseded_by` pointer.
- **Power-failure / pipeline crash mid-run**: On restart, the pipeline resumes from the last stage-boundary checkpoint, re-runs only in-flight scopes, and preserves findings already emitted.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST run a BMR audit as a pipeline of 5 stages in the order: `Ingest → Legibility & Classification → Structured Extraction & Summarisation → Compliance → Report & Resolution`. Within `Compliance`, `ALCOA` and `GMP` sub-capabilities MUST execute in parallel, and `Checklist-Synthesis` MUST execute after both complete.
- **FR-002**: The system MUST NOT run structured extraction or compliance on a page that fails the legibility gate; the gate's HITL MUST be narrow (re-upload / proceed-anyway only). Pages that pass continue downstream; flagged pages block only their own downstream work.
- **FR-003**: The system MUST run all automated compliance behaviours via the rule engine. A behaviour added to the pipeline MUST first be attempted as a rule-spec entry. New capability code is introduced only when the behaviour cannot be expressed against the rule-spec schema (this justification MUST appear in the feature's plan).
- **FR-004**: The system MUST provide a single final HITL checkpoint after Report & Resolution at which the reviewer resolves every Critical and Major finding. The export action MUST be blocked until all Critical and Major findings have been actioned.
- **FR-005**: The system MUST emit every finding with evidence: `document_id`, `page_number`, optional `region`, `capability_id`, `rule_id`, ALCOA+ principle, GMP category (if applicable), severity, raw value, and (where defined) expected value. For synthesised findings, the set of source `finding_id`s MUST also be recorded.
- **FR-006**: The system MUST compute a bounded re-execution plan when a correction is submitted at the final checkpoint. The plan MUST include only the rules whose `context_object` (directly or transitively) reads the corrected input. Full-pipeline re-runs for a single correction are a defect.
- **FR-007**: The system MUST present the re-execution plan (count of rules, scopes, estimated runtime) to the reviewer before starting and allow them to cancel.
- **FR-008**: The system MUST notify the reviewer of any of their prior resolutions that become stale due to a re-run (finding retracted, or raw value changed), and surface them for re-action.
- **FR-009**: The system MUST record every state transition (stage entered/exited, gate passed/failed, finding created/retracted, correction submitted, re-run planned/confirmed, export produced) into an append-only audit trail with actor, timestamp, and event schema version.
- **FR-010**: The system MUST generate document- or page-level summaries as a distinct capability driven by configurable templates. For the BPCR role, page-level summaries MUST be produced; for other roles, document-level summaries MUST be produced. Templates live in YAML, not code.
- **FR-011**: The system MUST provide the Consolidated Findings view: grouped by BPCR step (or document scope where no step applies), with collapsible ALCOA / GMP / Checklist-Adherence sections. Resolution controls MUST enforce a structured comment (`reason_type`, `observed_value_on_document`, `system_extracted_value`, optional `note`).
- **FR-012**: The system MUST NOT display or compute an overall compliance score. Findings are presented by severity and ALCOA/GMP tag only.
- **FR-013**: The pipeline MUST be resumable from the last stage-boundary checkpoint on restart without re-running completed scopes.
- **FR-014**: The pipeline's 5-stage orchestration MUST be declaratively wired. Adding a new capability invocation point within a stage MUST NOT require changes to the stage's control-flow code — only configuration and capability registration.
- **FR-015**: Existing single-document pipeline modes (`accuracy | quality | reasoning | byok | production`) MUST continue to function unchanged. Shared code touched by BMR features MUST carry regression tests for the older modes.
- **FR-016**: The system MUST allow an operator to opt a run into degraded-mode (process-replication) and MUST record the mode choice, trigger reason, and actor in the run's audit trail. Findings emitted in degraded-mode MUST be tagged `produced_in_mode: process_replication`.
- **FR-017**: The `Checklist-Synthesis` capability MUST attempt synthesis from ALCOA/GMP findings where the rule declares `synthesises_from`. Only checklist rules without any viable synthesis source MUST fall back to direct capability evaluation, using OCR-extracted content and document summaries.
- **FR-018**: Reviewer structured resolutions MUST be persisted as an accumulable feedback corpus accessible by rule-spec tuning and OCR fine-tuning workflows (precise storage schema: spec 004; consumption: spec 005).

### Key Entities *(include if feature involves data)*

- **BMRAuditRun**: One execution of the pipeline on one uploaded package. Attributes: `run_id`, `package_id`, `manifest_id`, `rule_set_version`, `mode` (`leverage` | `process_replication`), `current_stage`, `status`, timestamps, started-by user, predecessor-run (for revisions).
- **Pipeline Stage**: Enum `Ingest | LegibilityAndClassification | StructuredExtractionAndSummarisation | Compliance | ReportAndResolution`. Each has a status, entered/exited timestamps, and optionally sub-phase state (for the parallel Compliance branches).
- **Capability**: A named, atomic verifiable behaviour invoked by the orchestrator. Declared inputs / outputs / dependencies so the re-run planner can compute blast radius.
- **Rule**: A declarative entry in YAML with `rule_id`, `applicable_pages`, `context_object`, `evaluation` (capability + inputs + tolerances), `alcoa_principle`, `gmp_category`, `severity`, `fallback`, optional `synthesises_from`. Schema owned by Spec 005.
- **Finding**: A single verifiable claim produced by a capability (directly) or by Checklist-Synthesis (derivative). Fields include `run_id`, `logical_id`, `revision`, `capability_id`, `rule_id`, `scope` (page / document / BPCR step / entity), `evidence`, `alcoa_principle`, `gmp_category?`, `severity`, `raw_value`, `expected_value`, `tolerance_applied`, `hitl_state`, `source`: `direct | synthesised`, `source_finding_ids` (if synthesised), `produced_in_mode`.
- **Structured Resolution**: A reviewer's action on a finding (`Confirm` / `Dismiss` / `Correct`) carrying `reason_type`, `observed_value_on_document`, `system_extracted_value`, and an optional `note`. Stored immutably and appended to the feedback corpus.
- **Correction**: A reviewer-submitted change to an extracted source value. Binds to a `ReExecutionPlan`.
- **ReExecutionPlan**: An explicit, scoped plan of rules to re-evaluate given a `Correction`. Computed from the rule engine's reverse dependency graph over `context_object` inputs.
- **FeedbackSample**: A persisted tuple of `rule_id`, `resolution`, `input_context`, suitable for offline rule-tuning / OCR fine-tuning.
- **PipelineMode**: Run-level enum (`leverage` (default), `process_replication`) with its trigger reason in the audit trail.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A pilot BMR package of about 150–200 pages completes end-to-end leverage-mode audit in under 45 minutes including reviewer time (including legibility exceptions and final-checkpoint resolution).
- **SC-002**: On the pilot package, leverage-mode findings cover ≥ 95% of the gold-standard auditor baseline (no silent misses of Critical/Major items).
- **SC-003**: A single-value correction at the final checkpoint re-runs only the rules whose `context_object` touches that input and completes in under 30 seconds p95 for the pilot package size.
- **SC-004**: Across 10 consecutive runs on the same unchanged package + ruleset, the set of emitted findings is reproducible (same logical ids, same severities) with variance < 2% (attributable only to non-deterministic LLM/VLM calls).
- **SC-005**: Adding a new compliance behaviour expressible within the rule-spec schema requires zero Python-code changes; the feature is introduced by adding a YAML rule plus (if needed) a test fixture.
- **SC-006**: All existing single-document pipeline modes retain their pre-change regression-test pass rate (100% of the previously passing tests remain passing).
- **SC-007**: On restart after a crash mid-run, the pipeline resumes without re-running any scope already marked complete. Duplicate findings are not emitted.
- **SC-008**: All Critical and Major findings in the pilot package carry complete evidence (document + page + region-where-possible + rule-id + ALCOA principle + raw-vs-expected); synthesised findings additionally carry non-empty `source_finding_ids`. Audit passes a manual spot-check by the pharma SME.

## Assumptions

1. **Existing compliance framework works well enough to leverage.** ALCOA / GMP / rule-engine / OCR / VLM are expected to carry the heavy lifting. Degraded-mode (process-replication) is the recorded fallback if this turns out to be false on the pilot.
2. **BMR packages arrive as a single zip or a set of PDFs** with names and optional manifest hints. Spec 002 owns ingestion + classification.
3. **Pilot client's rules can be expressed declaratively** in the rule-spec schema (Spec 005). The product bet hinges on this; if it breaks, we escalate to degraded mode for that rule family.
4. **One reviewer per audit run in v1.** Multi-reviewer collaboration is a post-v1 feature.
5. **Exports are PDF (and optionally CSV of findings)** for v1. The export engine is owned by spec 004 and extends the existing review-store export path.
6. **Postgres is available** as the checkpointer and orchestration store. Filesystem JSON remains the document-of-record.
7. **Timeline pressure**: initial demo target ~3 weeks; full acceptance ~4 weeks. Scope decisions favour leverage-mode with declarative rules over new code. Degraded-mode is deferred unless needed.
