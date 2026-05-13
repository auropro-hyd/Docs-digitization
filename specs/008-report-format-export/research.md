# Research: Client-Aligned Compliance Report (Spec 008)

## Reference PDF — visual + structural analysis

**Source**: `context/2538104192 1/Checklist based Review_Compliance_Report (3).pdf`
**Pages**: 7
**Subject**: `2538104192-EHSII03.pdf` — Sertraline Hydrochloride (Micronized Grade – I) Batch 2538104192
**Generated**: 11/15/2025, 06:26 PM by Manoj Sankad via "Pharmix AI"
**Status flag**: "Document is Draft" (top-right of header on page 1)

### Page 1 — Header + first data rows

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ [logo]            BMR Compliance Intelligence Suite                          │
│                   Checklist based Review                                     │
│ TITLE OF DOCUMENT                                          Document is Draft │
├──────────────────────────────────────────────────────────────────────────────┤
│ Checklist Review Report                                                      │
│                                                                              │
│ ┌──────────────────┬─────────────────────────────────────────┐               │
│ │ Document         │ 2538104192-EHSII03.pdf                  │               │
│ │ Product          │ Sertraline Hydrochloride (Micronized…)  │               │
│ │ Batch No         │ 2538104192                              │               │
│ │ Date Of Validation│ 2025-11-15                             │               │
│ └──────────────────┴─────────────────────────────────────────┘               │
│                                                                              │
│ ┌─────────┬───────────┬──────────┬────────────────┬────────────┐             │
│ │Question │Compliance │Evidence  │Detailed Evid.  │Mitigation  │             │
│ │         │           │From Doc  │                │            │             │
│ ├─────────┼───────────┼──────────┼────────────────┼────────────┤             │
│ │Are all  │✅ Compliant│PAGE:103  │The document    │Not         │             │
│ │attach…  │           │          │contains…       │Applicable  │             │
│ └─────────┴───────────┴──────────┴────────────────┴────────────┘             │
│                                                                              │
│         Disclaimer Note: This document is electronically generated…          │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Pages 2–7 — continuation rows (no repeated header)

Subsequent pages carry only:
- Continuation of the same 5-column rule table
- Footer disclaimer

No page numbers, no per-section breaks, no agent grouping — it's one continuous table.

### Column behaviour, observed empirically

| Column | Width | Alignment | Vertical | Examples |
|---|---|---|---|---|
| Question | ~15-18% | left | top | "Are all the attachments enclosed with the BPCR?" |
| Compliance | ~10% | center | center | `✅ Compliant` (green), `⚠️ Action Required` (orange), `🟡 Attention` (amber) |
| Evidence From Document | ~10-12% | left | top | `PAGE:103`, `PAGE:36 to 42`, `PAGE:6, 9, 31, 59, 103`, `PAGE:6 to 34` |
| Detailed Evidence | ~45% | left | top | paragraph, multi-line, references PAGE:N inline |
| Mitigation | ~15-18% | left | top | `Not Applicable` OR 1-4-sentence action plan |

### Status taxonomy in the reference

Three distinct visual states are used:

1. **Compliant** — green check icon + "Compliant" text. Used on most rows.
2. **Action Required** — orange/red warning triangle + "Action Required" text. Used on one row in the reference (water-content check missing).
3. **Attention** — yellow attention icon + "Attention" text. Used on one row (output yield mismatch).

Note: the reference uses the singular word "Attention" without "Needs". Akhilesh's pointer says "needs attention". Spec adopts the pointer-side wording ("Needs Attention") for two reasons: (a) it's more explicit about action; (b) Akhilesh is the rule-domain expert, his wording wins.

### Evidence-column behaviour, observed

The reference DOES populate page numbers even for compliant rows (`PAGE:103`, `PAGE:36 to 42`, etc.). **Akhilesh's pointer says otherwise**: "If it is compliant, leave the page number empty."

Resolution: Akhilesh's directive overrides the reference. Rationale: when every compliant row carries N page references, the signal of failures-with-pages gets visually drowned. Empty page columns on compliant rows let the eye snap straight to the rows that need attention.

This is an INTENTIONAL deviation from the reference, documented in the spec and pinned by tests.

### Detailed Evidence column behaviour

For compliant rows in the reference, the Detailed Evidence text reads as a **multi-page summary**:

> "The document contains numerous attachments, including raw material requests (PAGE:36 to 42), packing material requests (PAGE:43 to 46, 68), SCADA data printouts (PAGE:48 to 67), in-process analysis reports (PAGE:70), analytical data (PAGE:72 to 79), operational checklists (PAGE:80 to 97), and the final Certificate of Analysis (PAGE:98 to 101). Furthermore, the 'BPCR REVIEW CHECK LIST' on PAGE:103, item 1, confirms that all attachments were enclosed, as verified by Manufacturing and Quality Assurance."

That's a 60-word summary referencing 7 page ranges. Akhilesh's pointer matches: "evidence should be summary of overall evidence include the highlights from two or three page/document level evidence output (after parallel processing of all relevant pages applicable for that rule)".

So we keep the inline `PAGE:N` references in the Detailed Evidence text (the column itself is still text-formatted, just the dedicated "Evidence From Document" column is empty for compliant rows).

### Mitigation column behaviour

For compliant rows: literal text "Not Applicable" (matching the reference verbatim — not "N/A", not blank, not "—").

For Attention row:
> "A formal investigation is required to reconcile the conflicting weight values reported on PAGE 13 (978.50 kg / 977.32 kg). The investigation must determine the correct final and intermediate weights by reviewing weighing logs and equipment printouts. CAPA actions should be implemented to address the root cause of the inconsistent data recording."

For Action Required row:
> "An investigation must be initiated to determine why the water content check at Step 67 (Step 0c) was not documented. The investigation should include interviewing the personnel involved and reviewing analytical records. A CAPA should be raised to prevent recurrence, possibly by reinforcing training on contemporaneous documentation and ensuring data is transcribed from analytical reports to the BPCR before review."

Pattern: 3-4 sentences, names the specific problem, prescribes investigation + CAPA + training.

### Footer

Every page carries:
> "Disclaimer Note : This document is electronically generated by Pharmix AI Printed By: Manoj Sankad Printed On: 11/15/2025, 06:26 PM"

Centered. Smaller font (≈8pt). Light grey.

## Current backend report path — gaps vs target

(Sourced from the parallel-research exploration of the codebase on 2026-05-14.)

### Current data structure (`backend/app/compliance/models.py`)

- **`ComplianceReport`** carries: `overall_score`, `model_score`, `review_adjusted_score`, `score_decomposition`, `score_methodology`, `executive_summary`, `total_findings`, `severity_counts`, `agent_reports`, `skipped_agents`, **`findings`** (flat list), `dedup_mode`, `audit_trail`.
- **`AgentReport`** carries: `score`, `model_score`, `review_adjusted_score`, `score_decomposition`, `total_rules`, `total_findings`, `severity_counts`, `category_scores`, `findings`, `all_evaluations`, `pages_reviewed`.
- **`ComplianceFinding`** carries: `finding_id`, `rule_id`, `rule_text`, `rule_category`, `severity`, `status`, `confidence`, `page_numbers`, `reasoning`, `evidence`, `description`, `recommendation`, `applicability_trace`, `resolved`, `hitl_status`, `hitl_note`, `evaluation_channels`, `visual_evidence`, `visual_regions`.
- **`RuleResult`** (per-rule rollup): `rule_id`, `rule_text`, `rule_category`, `agent`, `status`, `confidence`, `reasoning`, `evidence`, `applicability_trace`, `page_numbers`.

**Mapping to new shape**:

- The new `ReportRow.question` ← `RuleResult.rule_text` (or `ComplianceFinding.rule_text` when a rule has findings).
- `ReportRow.compliance_label` ← derived from `RuleResult.status` via the three-bucket mapping (compliant / non_compliant / uncertain|error|needs_review).
- `ReportRow.evidence_pages` ← `RuleResult.page_numbers` formatted as range string. **Empty when status == compliant.**
- `ReportRow.detailed_evidence` ← for compliant: synthesise summary from `RuleResult.evidence` + `RuleResult.reasoning`. For non-compliant/uncertain: `ComplianceFinding.evidence` + `ComplianceFinding.reasoning` for findings on the rule, concatenated.
- `ReportRow.mitigation` ← for compliant: `"Not Applicable"`. For non-compliant/uncertain: prefer `ComplianceFinding.recommendation`; fall back to `ComplianceFinding.mitigation_text` (new field — see FR-018); fall back to LLM synthesis.

### Current export path (`backend/app/api/routes/compliance.py`)

Today the `GET /api/compliance/{doc_id}/export` endpoint supports `?format=html` and `?format=md`, both rendered via:
- `_build_report_html()` (lines 801–1024)
- `_build_report_markdown()` (lines 1027–1178)

Both currently render the OLD shape (executive summary, per-agent scorecard, flat findings table, score callouts). Both ignore the reference layout.

**Plan**: rewrite both into renderer modules that consume the new `ReportDocument` shape. Add a third: `_build_report_pdf()` returning PDF bytes via WeasyPrint / ReportLab / similar.

### Current frontend display (`frontend/src/components/compliance/`)

Today the compliance view at `/compliance?doc=X` renders:
- `ExecutiveSummary` (the LLM-written prose summary with bullet risk lists)
- `AgentScorecard` × N (one per agent — large score badges, category breakdown)
- `FindingsTable` (flat filterable list)
- HITL controls embedded in the findings table

**Plan**: keep the score-rich top section (`AgentScorecard` panels + `ExecutiveSummary`) AS-IS. Replace the `FindingsTable` block with a new `<RuleTable>` component that consumes the same `ReportDocument` shape the renderer uses. Two presentations, ONE structural difference: the on-screen view carries the top score section; the exported artifact starts directly at the rule-centric table.

## Akhilesh's pointers — interpretation log

| Pointer | Interpretation | Spec ref |
|---|---|---|
| "update the export report format to this template" | Adopt the reference PDF's 5-column rule-centric layout for both export AND on-screen view | FR-001, FR-011 |
| "collapse findings into the rule table output" | One row per rule; multiple findings per rule merge to one row | FR-015 |
| "status — compliant, action required, needs attention" | Three buckets, status mapping per FR-002 | FR-002 |
| "If it is compliant, leave the page number empty" | Evidence-from-document column empty for compliant rows — INTENTIONAL deviation from the reference | FR-003 |
| "evidence should be summary of overall evidence include the highlights from two or three page/document level evidence output" | Compliant Detailed Evidence is a 2-3-sentence cross-page summary, not a per-page list | FR-004 |
| "mitigation — applicable only for action required and needs attention; refer to the report for presenting mitigation steps" | Mitigation column: "Not Applicable" for compliant; actionable 1-4-sentence text for the other two | FR-006, US3 |
| "remove score" | Stripped from the **export artifact** (PDF / HTML / Markdown returned by `/export`) ONLY. The **on-screen view** keeps `AgentScorecard` + `ExecutiveSummary` because score info is useful for internal HITL workflow ("which agent should I focus on?"). On-disk JSON keeps score fields (backward compatibility for downstream services). Three surfaces, three policies: JSON = all scores; on-screen = all scores; exported artifact = no scores. | FR-007, FR-011, FR-013 |

## Open questions

None at this draft. If the client provides a refreshed reference PDF, the spec must be re-anchored — but no current ambiguities require clarification before implementation starts.

## Out of scope for this feature

- Generation-time changes to rule evaluation (not in scope — this is a re-rendering feature).
- Per-agent reports (Akhilesh's directive collapses everything into one rule-centric table; per-agent navigation can return as a filter, not as separate documents).
- Email / share-link delivery of the PDF (this feature ships the artifact; downstream sharing is a separate concern).
- Auto-translation to other languages.
- DOCX / XLSX export. PDF + HTML + Markdown only.
- Bulk export across multiple docs (one doc at a time; the current endpoint signature is already per-doc).
