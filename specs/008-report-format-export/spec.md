# Feature Specification: Client-Aligned Compliance Report (Rule-Centric, No-Score)

**Feature Branch**: `008-report-format-export`
**Created**: 2026-05-14
**Status**: Draft
**Input**: Client-shared reference PDF `context/2538104192 1/Checklist based Review_Compliance_Report (3).pdf` + Akhilesh's 2026-05-13 pointers ("update the export report format to this template … collapse findings into the rule table … status: compliant / action required / needs attention … leave page number empty when compliant … evidence is a summary … mitigation only for non-compliant/uncertain … remove score")

## Background & Motivation

The current compliance report shows findings and rule evaluations as separate concerns: a per-agent score, an executive summary, an agent scorecard panel, and a flat findings table with severity badges. The reference PDF the client uses internally is **rule-centric**: one row per rule, five columns (Question | Compliance | Evidence From Document | Detailed Evidence | Mitigation), no per-agent scoring, no separate findings list. Findings ARE the rule rows.

This feature aligns the export AND the on-screen display with that shape. The on-disk JSON is kept additive (the renderer derives the new shape from existing models) so HITL history, telemetry, and integrations don't break.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Operator downloads a client-shareable report (Priority: P1) 🎯 MVP

A pharmaceutical-compliance operator runs an audit against a BPCR, opens the compliance view, and clicks "Download Report". The downloaded file matches the reference PDF's layout: header with logo + product/batch metadata, single rule-centric table, three-state compliance badges, mitigation column populated only where action is needed, no scores anywhere on the page.

**Why this priority**: This is the artifact the client actually consumes. Internal-only reporting features (scorecards, findings tables) have no value if the deliverable artifact doesn't match. P1 because it gates client-readiness.

**Independent Test**: With a completed compliance run cached on disk, downloading the export produces a PDF (or DOCX/HTML — format chosen below) whose visual diff against the reference PDF passes a 9-of-10 manual review: header layout, metadata block, column ordering, badge wording (`Compliant` / `Action Required` / `Needs Attention`), mitigation logic, no score field visible.

**Acceptance Scenarios**:

1. **Given** a completed compliance run for doc_id `X` with mixed compliant / non_compliant / uncertain rules, **When** the operator hits `GET /api/compliance/{X}/export?format=pdf`, **Then** the response is a PDF with one table-row per applicable rule, statuses bucketed as `Compliant` / `Action Required` / `Needs Attention`, no `overall_score` / `model_score` / `score_decomposition` text anywhere in the rendered output.
2. **Given** a compliant rule, **When** the row is rendered, **Then** the "Evidence From Document" column is **empty**, the "Detailed Evidence" column is a 2-3-sentence summary across pages, the "Mitigation" column is "Not Applicable".
3. **Given** a non-compliant rule with two findings on pages 70 and 71, **When** the row is rendered, **Then** "Evidence From Document" shows `PAGE:70, 71` (or the page range that covers the findings), "Detailed Evidence" describes the actual gap, "Mitigation" is an actionable next-step paragraph (CAPA / investigation / training).
4. **Given** an uncertain rule, **When** the row is rendered, **Then** the badge says "Needs Attention" (not "Uncertain"), pages are populated, mitigation is populated.

---

### User Story 2 — Operator views the rule-centric report on screen (Priority: P1)

The on-screen compliance view at `/compliance?doc=X` adopts the export's rule-centric layout AS its primary table — but unlike the export, it KEEPS the agent scorecard and executive summary above the table. The flat `FindingsTable` is replaced by the new `RuleTable` so the table itself matches the export 1:1. HITL controls (approve / reject / modify) move inline into each rule row.

**Why this priority**: Operators need a familiar working surface — scores guide their attention to riskier agents, and the executive summary frames the run. Removing those internally was over-stripping; only the downloaded artifact needs to be score-free (the client doesn't want our internal scoring on a shareable PDF). The on-screen view stays score-rich; the export strips them. P1 because the operator workflow change ships alongside the export.

**Independent Test**: Open `/compliance?doc=X` for a doc that's already been run; the page shows scorecard panels at top, then the new rule-centric table (replacing the old findings list); clicking a row exposes the per-page finding detail in an expand/drawer; HITL controls (approve/reject) operate on the rule.

**Acceptance Scenarios**:

1. **Given** the compliance result is cached for doc_id `X`, **When** the operator opens `/compliance?doc=X`, **Then** the page renders the agent scorecard + executive summary as today, followed by the new rule-centric `RuleTable` (which replaces the previous `FindingsTable`).
2. **Given** a rule has multiple findings across pages, **When** the operator expands its row, **Then** the per-page detail (page number, reasoning, evidence, HITL state) is visible inline; the row's summary collapses again when re-clicked.
3. **Given** an operator clicks "Approve" on a needs-attention row, **When** the action submits, **Then** the row's compliance badge updates without reload AND the rule's HITL state propagates to the next export (re-exports reflect the approved state).
4. **Given** the operator clicks "Download → PDF", **When** the PDF renders, **Then** scores DO NOT appear in the downloaded file even though they're still visible on the screen behind it.

---

### User Story 3 — Auditor inspects the rule row's mitigation guidance (Priority: P2)

For non-compliant and uncertain rows the Mitigation column carries actionable text: what investigation to run, which CAPA to consider, which training to reinforce. The text is derived from the per-finding `recommendation` and reasoning; for cases with insufficient signal an LLM-generated mitigation sentence is produced once and persisted with the run.

**Why this priority**: The reference PDF uses mitigation as the audit-trail entry that auditors and QA actually read. Without it the report is "here's what's wrong" with no "here's what to do". P2 because the structural layout is the P1 win — quality of mitigation text is a follow-on.

**Independent Test**: For a non-compliant rule whose findings carry `recommendation` fields, the rendered Mitigation column is the rendered recommendation. For a non-compliant rule whose findings have empty `recommendation`, the column shows a synthesised one-sentence guidance derived from the rule's reasoning + the violation class (missing field / data integrity / contemporaneous).

**Acceptance Scenarios**:

1. **Given** a non-compliant finding with `recommendation = "Document the missing water content check via investigation report"`, **When** the row renders, **Then** the Mitigation column contains that text (verbatim or lightly rephrased to match the reference's voice).
2. **Given** a non-compliant finding with `recommendation == ""`, **When** the row renders, **Then** the Mitigation column carries a generated guidance that names the rule's domain (CAPA / investigation / training) and is no longer than 4 sentences.
3. **Given** a compliant rule, **When** the row renders, **Then** the Mitigation column is "Not Applicable" (matching the reference verbatim).

---

### User Story 4 — Backwards-compatible JSON for HITL / telemetry / integrations (Priority: P2)

The on-disk `compliance_result.json` stays additive: the existing `ComplianceReport.findings`, per-agent `AgentReport`, and HITL state machine continue to work for any downstream consumer (telemetry sink, integrations, future analytics). The new rule-centric shape is **derived** at render time by a pure transform; the persisted JSON remains the source of truth.

**Why this priority**: Breaking the schema breaks all the existing HITL workflows, the telemetry summary's rule-by-rule view, and any auto-PR-tracker integrations downstream. P2 because it's a non-functional guard rail, not a user-facing win — but violating it makes US1/2 unshippable.

**Independent Test**: After this feature ships, the JSON at `data/documents/{doc_id}/compliance_result.json` for an existing doc still loads without migration, still carries `findings`, still surfaces `model_versions`, and the HITL-state preservation tests (existing in `tests/compliance/`) still pass.

**Acceptance Scenarios**:

1. **Given** a `compliance_result.json` written by today's pipeline, **When** the new export endpoint reads it, **Then** the rule-centric rows are produced without re-running the LLM.
2. **Given** the new export endpoint is called, **When** I `diff` the file's pre/post state, **Then** the on-disk JSON is unchanged.

---

### Edge Cases

- **Rule with zero findings AND status = `not_applicable`** → row is **excluded** from the report (we only show rules that actually evaluated). Otherwise the report has dozens of "doc has no manufacturing_operations section, rule skipped" rows that hide signal.
- **Rule with mixed findings across pages — some `compliant`, some `uncertain`** → the row's compliance is the **worst** status (matches the existing `assemble_agent_report` worst-status merge). Mitigation is populated. Evidence pages are the union.
- **Rule with `error` status (LLM failure)** → bucketed as `Needs Attention`, mitigation text indicates "Re-run the evaluation; LLM call failed."
- **Same rule_id appears across multiple agents** (cross-doc reconciliation rules) → each agent gets its own row; the agent name surfaces as a small chip on the row.
- **Very long rule text** (some rules are 300+ chars) → truncated to 2 lines with a tooltip / expander, NOT wrapped to 8 lines crushing the layout.
- **Rule with 30+ pages in findings** → page list renders as `PAGE:6 to 34` ranges, not 30 commas (matches reference exemplar from row "All steps followed within standard limits").
- **Mitigation text is missing AND can't be synthesised** (empty reasoning, empty recommendation) → fall back to "Review and remediate." rather than empty (every non-compliant/uncertain row MUST have a non-empty mitigation per Akhilesh's rule).
- **PDF generation fails** (renderer crash) → fall back to HTML/Markdown with a banner saying "PDF unavailable; rendered as HTML". Don't silently 500.
- **Doc has no rules in any applicable agent** (all zero-rule agents skipped — pre-PR#46 scenario) → report renders the metadata header + a single row stating "No applicable rules evaluated for this document type." Renderer still produces a file.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST render exactly five columns in the rule table, in this order: `Question`, `Compliance`, `Evidence From Document`, `Detailed Evidence`, `Mitigation`. No score, severity, or confidence column visible to the operator.
- **FR-002**: System MUST map rule status to one of three buckets for the `Compliance` column:
  - `compliant` → display label "Compliant" (green checkmark)
  - `non_compliant` → display label "Action Required" (orange warning)
  - `uncertain` OR `error` OR HITL `needs_review` → display label "Needs Attention" (amber attention)
  - `not_applicable` → row is excluded
- **FR-003**: For `Compliant` rows, the `Evidence From Document` column MUST be empty (per Akhilesh: client wants compliant rows to NOT cite page numbers — keeps signal on the failure cases).
- **FR-004**: For `Compliant` rows, the `Detailed Evidence` column MUST be a **summary across pages** — 2-3 sentences pulling highlights from per-page evaluations of that rule (e.g. "Across 35 manufacturing-operations pages, every Done by / Checked by cell carries a name and date; no strike-throughs or blank rows observed."). NOT a list of per-page entries.
- **FR-005**: For `Action Required` and `Needs Attention` rows, the `Evidence From Document` column MUST show page references in the reference format: `PAGE:N` for single pages, `PAGE:N, M, K` for sparse pages, `PAGE:N to M` for contiguous ranges of 3+ pages.
- **FR-006**: For `Action Required` and `Needs Attention` rows, the `Mitigation` column MUST contain actionable text (1-4 sentences) describing what an auditor should do next — CAPA, investigation, training, re-evaluation. For `Compliant` rows the Mitigation column MUST contain "Not Applicable" (verbatim, matching reference).
- **FR-007**: The **export artifact** (PDF / HTML / Markdown returned by `/export`) MUST NOT render any of `overall_score`, `model_score`, `review_adjusted_score`, `score_decomposition`, `score_methodology`, per-agent scores, category scores. These fields stay in the on-disk JSON for backward compatibility (FR-013), they stay visible on the on-screen compliance view (FR-011), they're stripped only from the downloaded artifact. Akhilesh's directive "remove score" applies to the report-as-exported, not the report-as-used-internally.
- **FR-008**: System MUST render the report header in the reference's structural shape: generic compliance-suite logo top-left, the product brand name centered in the header band (default: **"BMR Compliance Intelligence Suite"** — configurable via `AT_COMPLIANCE__REPORT_PRODUCT_NAME` env var), report title centered below ("Checklist based Review" — derived from `document_type` / agent name), "TITLE OF DOCUMENT" label top-left, "Document is Draft" top-right, then a metadata table with `Document`, `Product`, `Batch No`, `Date Of Validation` rows.
- **FR-009**: System MUST render the footer on every page matching the reference PDF: "Disclaimer Note : This document is electronically generated by Pharmix AI Printed By: {operator} Printed On: {generated_at}".
- **FR-010**: System MUST emit the export in **PDF format** as the primary deliverable (Akhilesh's directive "update the export report format to this template" — the reference is a PDF, so the export must be PDF too). HTML and Markdown formats remain available as fallback formats for ops users.
- **FR-011**: On-screen compliance view (`/compliance?doc=X`) MUST render the rule-centric table (matching FR-001 through FR-006 + FR-014…FR-017). The flat `FindingsTable` is replaced by the new `RuleTable`. `AgentScorecard` and `ExecutiveSummary` (with scores) **stay** on the on-screen view — score information is operationally useful for internal HITL workflows even though it's stripped from the downloaded artifact. Score display is the only structural difference between the on-screen view and the export.
- **FR-012**: On-screen view MUST preserve HITL controls — for any non-compliant/uncertain row the operator can approve, reject, or modify the verdict; the action updates the rule's HITL state and re-renders the badge.
- **FR-013**: On-disk `compliance_result.json` shape MUST remain backward-compatible: `ComplianceReport`, `AgentReport`, `RuleResult`, `ComplianceFinding` Pydantic models keep their current fields; the rule-centric shape is **derived at render time** by a pure transform function. Score fields stay in the JSON for legacy consumers but are not consumed by the new export / view code paths.
- **FR-014**: System MUST exclude rules with `status="not_applicable"` from the rendered report (matches the reference; otherwise the table is dominated by "rule doesn't apply to this doc" rows).
- **FR-015**: System MUST collapse multiple findings per rule into ONE row: page numbers are the union, evidence/reasoning are concatenated or summarised, mitigation is the strongest recommendation across findings.
- **FR-016**: System MUST render rule text in the `Question` column verbatim (the existing `AuditRule.text` field). No abbreviation; long rule text wraps within the cell with a tooltip / expander.
- **FR-017**: System MUST handle the multi-agent rule case (cross-doc reconciliation): each agent's evaluation of the rule is a separate row, with the agent name (`ALCOA+`, `GMP`, `Checklist`, `SOP`, `Cross-Page`) chip-marked on the row for disambiguation.
- **FR-018**: System MUST persist any LLM-synthesised mitigation text into the on-disk JSON (a new optional `ComplianceFinding.mitigation_text` field with default `""`) so re-export is idempotent — the LLM call only happens once per finding.
- **FR-019**: System MUST log a structured telemetry event `compliance.report_rendered` per export request, capturing `format`, `row_count`, `compliant_count`, `action_required_count`, `needs_attention_count`, so post-run dashboards can compare exports against the underlying JSON.
- **FR-020**: System MUST recognise existing HITL overrides when deriving the row's status — if an operator approved a non-compliant finding, the rule's row badge MUST reflect the approval state (existing review-adjusted logic remains the source of truth for HITL-applied verdicts).

### Key Entities *(include if feature involves data)*

- **ReportRow** *(new, render-time only — not persisted)*: A single row of the client-aligned report.
  - `question` (str) — rule text
  - `compliance_label` (str) — `"Compliant"` / `"Action Required"` / `"Needs Attention"`
  - `compliance_kind` (enum: `compliant` / `action_required` / `needs_attention`) — for badge styling
  - `evidence_pages` (str) — page references string (empty when compliant)
  - `detailed_evidence` (str) — paragraph (summary if compliant; reasoning if not)
  - `mitigation` (str) — actionable text or "Not Applicable"
  - `agent` (str) — agent display name (chip on multi-agent rows)
  - `rule_id` (str) — for the on-screen expand drawer + HITL routing
- **ReportDocument** *(new, render-time only)*: The complete report payload feeding the renderer.
  - `header` — title, doc metadata, draft flag
  - `rows` — list of `ReportRow`
  - `footer` — operator name, generation timestamp
- **Renderer** *(new abstraction)*: Pure function `(ComplianceReport, format) → bytes` returning PDF / HTML / Markdown. Lives in `backend/app/compliance/report_renderer/`.
- **ComplianceFinding** *(modified — additive only)*: Adds optional `mitigation_text: str = ""` field so synthesised mitigation persists across re-exports.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A new operator who has never seen the system can produce a client-shareable compliance report in ≤ 60 seconds from "compliance run finished" to "PDF downloaded". (Currently the path is: open page, scroll past scorecard, scroll past exec summary, find findings table, scroll-filter for non-compliant rows, click export dropdown, choose format — measurably slower.)
- **SC-002**: Visual-diff QA against the client reference PDF passes on 9-of-10 layout checks (column count + order, badge wording, footer text, header table, mitigation column logic, no-score, page-empty-when-compliant, range syntax `PAGE:N to M`, multi-line wrapping, font choice within ±1 size point).
- **SC-003**: Round-trip equality: load `compliance_result.json`, render to PDF, parse PDF text back, group by rule — every rule that was in the JSON appears in the PDF and vice-versa (excluding `not_applicable`).
- **SC-004**: Re-running the export on a doc that has had HITL approvals applied produces a PDF where the approved rules show as `Compliant` (not the underlying model's `non_compliant`). Idempotent: two consecutive exports produce byte-identical PDFs (modulo the `Printed On` timestamp).
- **SC-005**: Mitigation text appears on 100% of `Action Required` and `Needs Attention` rows. Zero blank-mitigation cells on those rows (LLM-fallback path covers any case where finding.recommendation was empty).
- **SC-006**: Per-export LLM cost cap: total `mitigation_text` synthesis spend per export ≤ $0.50 for a typical 25-rule BPCR audit. (Implementation: persist `mitigation_text` in `ComplianceFinding` after the first synthesis so re-exports use the cached text.)
- **SC-007**: No regression in existing HITL workflow tests (the existing `tests/compliance/test_*.py` suite passes unchanged).

## Assumptions

- Users have a modern browser (Chrome / Firefox / Safari latest two majors). Server-side PDF rendering is the default; no client-side fallback needed for v1.
- The existing `assemble_agent_report` "worst status wins per rule" merge logic is correct and stays as-is — this feature only re-renders the merged output, it doesn't change the underlying merge.
- The client's reference PDF (`Checklist based Review_Compliance_Report (3).pdf`) is the source of truth for layout. If the client provides an updated reference, this spec must be re-anchored against the new one before changes ship.
- `document_type` from the segmentation step is good enough to drive the report's header title ("Checklist based Review", "ALCOA+ Compliance Report", etc.). If segmentation classifies the doc as `unknown`, the renderer falls back to "Compliance Report".
- The pipeline's existing `ComplianceFinding.recommendation` is the primary source for mitigation text. Existing rule yamls already use this field where authors wrote remediation guidance.
- Operator name for the disclaimer footer is sourced from `X-Actor-Id` request header or `actor_id` request scope; falls back to "System" when missing.
- Removing the score from the rendered report does NOT mean removing it from the underlying model. Scores stay computable for any downstream service that wants them — they just don't appear in the operator-facing artifact.
- The reference PDF uses A4 / letter portrait. Renderer defaults to A4 portrait; landscape is reserved as a fallback when the rule table has rules wider than 12 lines of text.
- Logo asset is a **generic compliance-suite mark** (neutral, non-client-specific) shipped with the renderer at `backend/app/compliance/report_renderer/assets/logo.png`. If missing at runtime, the renderer falls back to a text-only header and emits a `compliance.report_rendered.missing_asset` warning. The asset path is configurable via `AT_COMPLIANCE__REPORT_LOGO_PATH`.
- Product brand name in the header band defaults to **"BMR Compliance Intelligence Suite"** — sits below the existing "Pharmix AI" brand that already appears in the disclaimer footer (two brand layers: masthead = product, footer = engine). Configurable via `AT_COMPLIANCE__REPORT_PRODUCT_NAME` so the brand can be re-skinned without a code change.
