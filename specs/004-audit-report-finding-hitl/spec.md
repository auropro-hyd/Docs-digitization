# Feature Specification: BMR Audit Report & Finding-Level HITL

**Feature Branch**: `004-audit-report-finding-hitl`
**Created**: 2026-04-17
**Last Revised**: 2026-04-17 (v2 — adds structured-resolution schema, consolidated step-grouped UI, FeedbackSample corpus, per Constitution v1.1.0 Principles VIII + IX)
**Status**: Draft
**Input**: User description: "Consolidated BMR audit report (BUC §16) with a single grouped view (by BPCR step, with collapsible ALCOA / GMP / Checklist sub-sections), structured-resolution comment (reason_type + observed/extracted values) required on every Dismiss and Correct, a feedback corpus seeded by every resolution for rule-spec tuning and OCR fine-tuning, and the evidence-linked bounding box viewer."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reviewer walks the final audit report and takes action on each finding (Priority: P1)

At the final HITL checkpoint, the reviewer opens the consolidated report for the package, reads each finding in a structured layout, and for each one chooses Confirm, Dismiss-with-reason, or Correct. The report tracks completion and refuses to export until every Critical and Major finding has been actioned.

**Why this priority**: This is the single checkpoint that gates the audit output. Without it the pipeline has no way to produce a defensible report.

**Independent Test**: Open a completed audit's final report, action every finding, export. Verify (a) the exported document lists each finding with reviewer action, identity, and timestamp; (b) attempting to export without actioning a Critical/Major finding is blocked with a clear message.

**Acceptance Scenarios**:

1. **Given** the pipeline has reached the Report stage, **When** the reviewer opens the report, **Then** findings are grouped by BPCR step (or by document scope for findings that are not step-bound), and inside each step group the findings appear under collapsible **ALCOA / GMP / Checklist-Adherence** sub-sections. Severity counts are shown per group. An alternative "flat by severity" view is available as a toggle for quick triage.
2. **Given** the reviewer clicks Confirm on a finding, **When** the action is recorded, **Then** the finding state moves to Confirmed and a StructuredResolution row is persisted carrying reviewer identity, timestamp, `action: CONFIRM`, and optional note. No reason_type is required for Confirm.
3. **Given** the reviewer clicks Dismiss on a finding, **When** the resolution form appears, **Then** the reviewer MUST select a `reason_type` (`OCR_MISREAD | ACCEPTABLE_VARIANCE | DUPLICATE_FINDING | OUT_OF_SCOPE | RULE_MISCONFIGURED | OTHER`). For `OCR_MISREAD` and `ACCEPTABLE_VARIANCE`, the reviewer MUST also supply `observed_value_on_document`; `system_extracted_value` is auto-snapshot from the finding and immutable. Free-text-only dismissals are rejected with a clear error.
4. **Given** every Critical and Major finding has a StructuredResolution whose `superseded_by` is null, **When** the reviewer clicks Export, **Then** the consolidated report is produced (PDF + structured data bundle) with all resolutions, identities, timestamps, and reason types carried through.
5. **Given** at least one Critical or Major finding is unactioned or has a stale resolution (superseded by re-run), **When** the reviewer clicks Export, **Then** export is blocked with a message listing the pending findings.
6. **Given** any StructuredResolution is persisted, **When** the feedback corpus path runs, **Then** a FeedbackSample row is created referencing the rule_id, rule_version, resolution action + reason_type, input-context digest, and an immutable snapshot of the original finding including its evidence refs. The FeedbackSample enables later rule-spec tuning (Spec 005) and OCR fine-tuning without further reviewer intervention.

---

### User Story 2 - Reviewer verifies a finding by viewing the highlighted evidence on the source page (Priority: P1)

The reviewer selects a finding and is shown the source document, the specific page, and a highlighted region on the page where the extracted value lives. They can toggle between the BPCR source and the cross-document source for reconciliation findings and zoom into the region.

**Why this priority**: Per the constitution, every finding is evidence-bound. The ability to see the evidence on the page is what makes the finding actionable, not just readable.

**Independent Test**: Click a reconciliation finding. Verify the viewer opens with both source pages highlighted at the correct regions. Click a single-source finding. Verify the single source page highlight is shown.

**Acceptance Scenarios**:

1. **Given** a finding has a region (bounding box or text span) on one or more source pages, **When** the reviewer selects it, **Then** the viewer jumps to the first source page at the correct page number and highlights the region.
2. **Given** a finding has multiple source references (cross-document), **When** the reviewer selects it, **Then** the viewer offers navigation between sources (e.g., "source 1 of 2") without losing the highlight.
3. **Given** a finding has no extractable region (page-level only), **When** the reviewer selects it, **Then** the viewer still opens the correct page with a clear "no specific region extracted" marker rather than silently failing.

---

### User Story 3 - Reviewer corrects an extracted value and triggers selective re-evaluation (Priority: P1)

For a finding where the underlying extraction is wrong, the reviewer chooses Correct, edits the extracted value (not the finding text), sees a preview of which other findings will be re-evaluated, confirms, and after a brief re-evaluation the report updates: some findings resolve, new ones may appear, others are unchanged.

**Why this priority**: Without Correct-and-re-evaluate, the reviewer has only Confirm/Dismiss — which means a known-wrong extraction poisons every downstream check with no recovery.

**Independent Test**: Stage a finding driven by a known extraction error. Use Correct, change the value, confirm. Verify: (a) re-evaluation scope preview matches the actual capabilities that re-ran; (b) stale findings are retracted with a retraction-reason log entry; (c) new findings (if any) appear with a "new since correction" marker; (d) findings whose inputs were untouched are byte-identical.

**Acceptance Scenarios**:

1. **Given** a finding's source value is editable, **When** the reviewer clicks Correct, **Then** the system shows the original extracted value, allows edit, and on submit computes the re-execution scope from spec 003's rule dependency metadata.
2. **Given** re-execution scope is computed, **When** the reviewer confirms, **Then** the system executes selective re-run and updates the report in place, marking retractions and additions.
3. **Given** a correction invalidates a previously Confirmed finding, **When** re-evaluation surfaces that invalidation, **Then** the reviewer is required to re-review that finding before export is permitted.
4. **Given** the correction invalidates a Legibility gate decision for the page (e.g., an "illegible" correction becomes "no, actually illegible"), **When** re-evaluation runs, **Then** the page re-enters the Legibility-and-Classification stage narrow HITL (re-upload / proceed) and downstream findings for that page are marked stale.

---

### User Story 4 - Reviewer exports the final audit report in the QC-Head-approved format (Priority: P1)

The reviewer clicks Export and receives a consolidated PDF (plus a structured data bundle) conforming to BUC Section 16: executive summary, findings by severity, findings by ALCOA+ tag, rule-evaluation appendix, system-confidence appendix, and package metadata. The PDF explicitly does not contain an overall compliance score.

**Why this priority**: The exported document is the deliverable the QC Head signs. Its format is explicitly stakeholder-specified.

**Independent Test**: Export a completed audit. Verify (a) PDF contains the specified sections in order; (b) structured bundle JSON is schema-valid; (c) no overall compliance score is present anywhere; (d) every finding row links back to evidence.

**Acceptance Scenarios**:

1. **Given** the reviewer has actioned all Critical/Major findings, **When** they click Export, **Then** a PDF is produced with sections: Package Metadata, Executive Summary, Findings by Severity, Findings by ALCOA+ Tag, Rule Evaluation Appendix, System Confidence Appendix.
2. **Given** the PDF is produced, **When** any section is inspected, **Then** no overall compliance score or rolled-up pass/fail is present; summaries are counts by severity and by ALCOA+ tag only.
3. **Given** export completes, **When** the structured bundle is opened, **Then** it carries the full finding list (including Dismissed findings with reasons), the reconciliation-result log, the HITL action history, and package/manifest identifiers sufficient to reproduce the report.
4. **Given** export has completed once, **When** the reviewer attempts to action findings further, **Then** the system treats the original export as immutable; any further actions produce a new revision of the report with a linked predecessor.

---

### User Story 5 - Reviewer filters and navigates a large finding set (Priority: P2)

On a package with hundreds of findings, the reviewer filters by severity, ALCOA+ tag, document, stage-of-origin, and HITL state so they can triage Critical findings first, then walk Major, etc.

**Why this priority**: Usable on the pilot (< 50 findings) without filters; essential as the tool scales.

**Independent Test**: Load a synthetic package with 200+ findings. Apply each filter; verify counts and sort stability.

**Acceptance Scenarios**:

1. **Given** the finding list is displayed, **When** the reviewer applies a filter (severity / ALCOA tag / document / stage / HITL state), **Then** only matching findings remain visible and the total count updates.
2. **Given** a filter is active, **When** the reviewer actions a finding, **Then** the list does not reshuffle unexpectedly; the actioned finding either stays or is removed based on the filter, and focus moves to the next matching item.

### Edge Cases

- What happens if the reviewer's session disconnects mid-report? All actions are persisted per action; on reconnection the reviewer resumes with no loss of prior actions.
- What happens if a correction triggers a large re-execution scope (e.g., 50+ findings)? The scope preview shows the count and offers a Cancel option before re-execution starts.
- What happens if re-evaluation fails (e.g., a capability errors)? The failed capability is surfaced as a system-confidence finding in its own right; the reviewer can retry just that capability.
- What happens when two reviewers (where multi-reviewer is eventually supported — out of scope for v1 but design hook) take conflicting actions on the same finding? V1 enforces one-reviewer-per-package; future versions will need conflict resolution.
- What happens if a finding has been retracted by re-evaluation but the reviewer had Dismissed it already? The Dismiss action is preserved in history; the retraction note explains the retraction superseded the dismissal.
- What happens when export is attempted with all Critical findings Dismissed? Export is permitted because dismissal is an action; the Dismissed-with-reason set is explicitly listed in the Executive Summary so reviewer justification is visible.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST present the final audit report in a **consolidated view grouped by BPCR step** (or by document scope for findings not bound to a step), with collapsible **ALCOA / GMP / Checklist-Adherence** sub-sections per group. A toggle to "flat by severity" (Critical → Major → Minor → Observation, then ALCOA+ tag) MUST also be available. Counts per group MUST be shown.
- **FR-002**: System MUST offer exactly three HITL actions on each finding: `CONFIRM`, `DISMISS`, `CORRECT`. All three produce a `StructuredResolution`. `CONFIRM` requires only an optional `note`. `DISMISS` requires `reason_type` from the controlled vocabulary and, when `reason_type ∈ { OCR_MISREAD, ACCEPTABLE_VARIANCE }`, requires `observed_value_on_document`. `CORRECT` requires `reason_type`, `observed_value_on_document`, and the corrected value (opening the correction flow from spec 001 / spec 003).
- **FR-003**: Every StructuredResolution MUST record reviewer identity, server-assigned timestamp, finding_logical_id, finding_revision, action, reason_type (when applicable), observed_value_on_document (when applicable), an IMMUTABLE snapshot of the finding's `system_extracted_value`, and optional note. Free-text-only resolutions (no structured fields) MUST be rejected by the API.
- **FR-004**: System MUST NOT permit export of the report while any Critical- or Major-severity finding remains unactioned.
- **FR-005**: System MUST display, for each finding, resolvable evidence links: source document(s), page number(s), and highlighted region(s) where extractable. A finding with no region MUST still display the correct source page with an explicit "no specific region extracted" marker.
- **FR-006**: System MUST support multi-source evidence navigation (previous / next source) for findings emitted by the cross-document reconciliation engine.
- **FR-007**: On Correct action, system MUST display the re-execution scope (count of findings to re-evaluate, list of affected capabilities) before running re-evaluation.
- **FR-008**: On re-evaluation completion, system MUST mark findings retracted by the correction with a retraction note and mark findings newly produced with a "new since correction" marker for reviewer awareness.
- **FR-009**: When a correction invalidates a previously Confirmed finding, system MUST require the reviewer to re-review that finding before export is permitted.
- **FR-010**: Exported report MUST include sections in this order: Package Metadata, Executive Summary, Findings by Severity, Findings by ALCOA+ Tag, Rule Evaluation Appendix, System Confidence Appendix.
- **FR-011**: Exported report MUST NOT contain an overall compliance score, rolled-up pass/fail, or percentage-compliant metric anywhere in the PDF or structured data.
- **FR-012**: Export MUST produce (a) a PDF document and (b) a structured data bundle containing the full finding list (including Dismissed findings with reasons), the reconciliation-result log, the HITL action history, and sufficient package / manifest identifiers to reproduce the report.
- **FR-013**: Once exported, the report MUST be treated as immutable; subsequent actions produce a new revision linked to the predecessor, not an in-place edit of the exported artifact.
- **FR-014**: System MUST provide filters on the finding list for severity, ALCOA+ tag, document, stage-of-origin, and HITL state. Filter changes MUST NOT reshuffle previously actioned findings.
- **FR-015**: System MUST persist every HITL action at action time; an in-progress reviewer session that disconnects MUST NOT lose prior actions on reconnect.
- **FR-016**: Export MUST embed, in the structured bundle, the manifest version, rule-set version, and capability-output snapshots used, so the report is reproducible even after later configuration changes.
- **FR-017**: System MUST create exactly one FeedbackSample per StructuredResolution (idempotent on resolution_id). The FeedbackSample MUST carry rule_id, rule_version, resolution action + reason_type, input-context digest, `observed_vs_extracted: { observed, extracted }` (when applicable), and an immutable snapshot of the original finding with its evidence refs. FeedbackSamples are NOT affected by subsequent re-runs or corrections; each resolution's sample is durable.
- **FR-018**: System MUST expose the feedback corpus to internal tooling (Spec 005 authoring skill, OCR fine-tuning jobs) via a stable API — scoped per run and globally (admin-only). The corpus MUST support query by rule_id, reason_type, and date range.
- **FR-019**: When a re-run supersedes a StructuredResolution (marking it stale), system MUST surface the stale resolution in a "Needs re-action" tray in the consolidated view, with a visible indicator (not buried in the full finding list), and MUST NOT silently retract the resolution.
- **FR-020**: The consolidated view MUST allow the reviewer to jump from a synthesised Checklist finding to its source ALCOA/GMP findings (via `source_finding_ids`), and from a source finding back to any synthesised descendants, to make synthesis chains auditable.

### Key Entities *(include if feature involves data)*

- **AuditReport**: The consolidated deliverable for a package. Carries report identifier, package identifier, manifest / rule-set version references, timestamps (created, last-updated, exported), revision number, predecessor report reference if any, and immutable flag once exported.
- **ReportSection**: A declared section of the report (Package Metadata, Executive Summary, Findings by Severity, Findings by ALCOA+ Tag, Rule Evaluation Appendix, System Confidence Appendix). Declared in configuration so the template can be versioned.
- **StructuredResolution** (canonical name; `FindingAction` is a synonym retained for compatibility): An immutable record of a reviewer action on a finding. Carries reviewer identity, server-assigned timestamp, finding_logical_id + finding_revision, action (`CONFIRM | DISMISS | CORRECT`), reason_type (when action ∈ DISMISS/CORRECT), observed_value_on_document (when reason_type requires it), immutable snapshot of `system_extracted_value`, optional note, `correction_id` (when action = CORRECT), and `superseded_by` pointer (set when a re-run makes the resolution stale). The authoritative schema lives in spec 001 data-model.md §1.5.
- **FeedbackSample**: A training-signal record derived one-to-one from each StructuredResolution. Carries rule_id, rule_version, resolution_action, reason_type, input-context digest, observed-vs-extracted pair, and an immutable snapshot of the original finding including evidence refs. Durable across corrections and re-runs.
- **EvidenceLink**: A reference a reviewer can follow to see where a finding's claim is grounded. Carries document identifier, page number, region (bounding box or text span), and source-type marker (primary / cross-reference).
- **CorrectionWorkflow**: A transient entity representing an in-progress Correct action. Carries the target source value reference, the proposed new value, the computed re-execution scope, and a confirmation / cancel state.
- **ReportRevision**: Links a report to its predecessor when post-export edits occur. Carries revision number, predecessor reference, and the diff of findings / actions between revisions.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On the pilot package, a reviewer can walk every finding and action it (Confirm / Dismiss / Correct) in under 30 minutes p95, measured across five reviewer dry-runs.
- **SC-002**: Every finding in the exported report is traceable to resolvable evidence (document + page + region or page-only with explicit marker) — 100% verifiable by automated schema check.
- **SC-003**: Zero Critical or Major findings can appear in an exported report without a reviewer HITL action — verifiable by automated schema check.
- **SC-004**: On a Correct action affecting the pilot package, re-evaluation preview appears in under 2 seconds and re-evaluation itself completes in under 30 seconds p95.
- **SC-005**: Exported report PDF contains zero occurrences of any text resembling an overall compliance score — verifiable by a text-match assertion against a deny-list of phrases.
- **SC-006**: Exported structured bundle is reproducible: re-loading the bundle into the system reproduces the identical report display (byte-identical section ordering and finding grouping).
- **SC-007**: Session disconnect integration test: reviewer actions N findings, session is killed, reconnect; all N actions are restored.
- **SC-008**: Filter / navigation test: with a 250-finding synthetic package, applying any single filter and scrolling produces a stable list ordering across five dry-runs.
- **SC-009**: Every StructuredResolution automatically produces exactly one FeedbackSample (idempotent on resolution_id). Verifiable by an integration test that records N resolutions and asserts N feedback samples with non-null rule_id + reason_type + finding snapshot.
- **SC-010**: The consolidated step-grouped view is the default for every reviewer; the legacy "flat by severity" path remains available only via toggle. Verified by a UI smoke test asserting the step-grouped view renders for a fixture with findings bound to BPCR steps.
- **SC-011**: When a re-run supersedes a prior resolution, the "Needs re-action" tray is populated within 5 seconds of the re-run completing, and blocks export until cleared.

## Assumptions

- The PDF template follows BUC §16 section order and is versioned as configuration; the concrete layout (typography, logos) is within design-system scope, not spec scope.
- Extracted regions (bounding boxes) are available from upstream OCR/VLM for most findings; findings without regions are a known minority and have page-level links.
- The existing review-store subsystem is reused for HITL action persistence.
- The existing document viewer subsystem is extended to support region highlighting; a net-new viewer is not required.
- Export produces artifacts the reviewer downloads; long-term retention of exported PDFs is handled by an external document management system out of this spec's scope.
- Multi-reviewer concurrent editing of the same package is out of scope for v1; the system assumes one reviewer per audit.
- The structured bundle format is JSON (or equivalent); the schema is versioned and lives in configuration.
- "Stage-of-origin" on a finding refers to the pipeline stage defined in spec 001.
