# Implementation Plan: Client-Aligned Compliance Report (Spec 008)

## Summary

Adopt the client-shared reference PDF's rule-centric layout for both the export AND the on-screen compliance view. Collapse findings into rule rows. Three-state compliance taxonomy (Compliant / Action Required / Needs Attention). No score visible to the operator. Mitigation column populated only for non-compliant + uncertain rows.

Implementation is a renderer rewrite + a new on-screen component, both consuming the same `ReportDocument` shape derived from the existing `ComplianceReport` JSON via a pure transform. The on-disk JSON shape is preserved (one additive field on `ComplianceFinding` for cached mitigation text).

## Technical Context

| | |
|---|---|
| Language / runtime | Python 3.13 (backend), TypeScript / Next.js 15 (frontend) |
| New backend deps | `weasyprint` for PDF rendering — pure-Python + cairo, gives us HTML→PDF without a Chromium dependency |
| New frontend deps | None — existing component primitives (Table, Badge, Tooltip) suffice |
| Test framework | pytest (backend), no new frontend test infra required |
| Performance target | render < 2s for 25-rule BPCR audit; LLM-fallback mitigation synthesis < 10s amortised across export run |
| Cost target | ≤ $0.50 of LLM spend per export (see SC-006) |
| Concurrency | single-doc render is sequential; no parallelism inside the renderer |

## Constitution Check

Reviewed `.specify/memory/constitution.md`. Relevant principles:

- **Principle I (Human Auditor Mirror, Not Replacement)** — this feature surfaces the auditor's review artifact more clearly; doesn't replace human judgement.
- **Principle II (Staged Pipeline with Hard Gates)** — this is post-pipeline; no impact on segmentation / OCR / evaluation stages.
- **Principle VII (Existing Framework Is the Backbone)** — additive only. `ComplianceReport`, `AgentReport`, `ComplianceFinding`, `RuleResult` unchanged except one new optional field. Score fields kept on disk for downstream services. The new shape is rendered, not migrated.
- **Principle IX (Rule-as-Data, Not Rule-as-Code)** — mitigation text source priority respects the rule author's `recommendation` field first; LLM fallback only when the rule yaml didn't include guidance.

No constitution violations. No amendment needed.

## Project Structure

### Documentation (this feature)

```
specs/008-report-format-export/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── tasks.md
├── contracts/
│   └── api-contract.md
└── checklists/
    └── requirements.md
```

### Source code (web application — backend + frontend)

```
backend/
├── app/
│   ├── compliance/
│   │   ├── models.py                          # additive: mitigation_text field on ComplianceFinding
│   │   └── report_renderer/                   # NEW package
│   │       ├── __init__.py
│   │       ├── builder.py                     # build_report_document() pure transform
│   │       ├── status_bucket.py               # _bucket_status() + HITL override resolution
│   │       ├── page_formatter.py              # _format_pages() — "PAGE:6 to 34" syntax
│   │       ├── mitigation.py                  # _pick_mitigation() + LLM-synth fallback
│   │       ├── summary.py                     # _summarise_compliant() — cross-page summary
│   │       ├── render_html.py                 # HTML renderer (Jinja2 template)
│   │       ├── render_pdf.py                  # PDF via weasyprint(HTML → PDF)
│   │       ├── render_md.py                   # Markdown renderer
│   │       ├── templates/
│   │       │   ├── report.html.j2             # Jinja2 — used by HTML AND PDF paths
│   │       │   └── styles.css                 # Print-CSS @page rules, table styling
│   │       └── assets/
│   │           └── logo.png                   # generic compliance-suite mark (non-client-specific)
│   └── api/
│       └── routes/
│           └── compliance.py                  # update /export; add /report-rows, /mitigation/synthesize, /preview
└── tests/
    └── compliance/
        └── report_renderer/                   # NEW test package
            ├── test_status_bucket.py          # status mapping + HITL overrides
            ├── test_page_formatter.py         # range-vs-comma syntax
            ├── test_builder.py                # full build_report_document() shapes
            ├── test_mitigation.py             # recommendation priority + cache + LLM fallback
            ├── test_render_html.py            # HTML structure + 5-column table
            ├── test_render_pdf.py             # PDF content via pypdf — verify text in output
            └── test_render_md.py              # Markdown shape

frontend/
├── src/
│   ├── app/compliance/
│   │   └── page.tsx                           # rewrite the layout: drop scorecard panels, mount RuleTable
│   ├── components/compliance/
│   │   ├── rule-table.tsx                     # NEW — replaces FindingsTable on the default view
│   │   ├── rule-row.tsx                       # NEW — one row component
│   │   ├── compliance-badge.tsx               # NEW — 3-state badge (Compliant / Action Required / Needs Attention)
│   │   ├── rule-expand-drawer.tsx             # NEW — per-rule expand showing per-page detail + HITL controls
│   │   ├── report-preview-iframe.tsx          # NEW — embeds /preview PDF for in-browser preview
│   │   ├── compliance-report.tsx              # update: keep AgentScorecard + ExecutiveSummary, swap FindingsTable → RuleTable, add Export dropdown
│   │   ├── agent-scorecard.tsx                # KEEP — score panel stays on the on-screen view
│   │   ├── executive-summary.tsx              # KEEP — exec summary with scores stays on the on-screen view
│   │   └── findings-table.tsx                 # DELETE — replaced by rule-table.tsx
│   ├── types/
│   │   └── compliance.ts                      # add ReportRow / ReportDocument types mirroring backend
│   └── lib/
│       └── api.ts                             # add getReportRows(), synthesizeMitigation()
```

## Complexity Tracking

| Aspect | Concern | Mitigation |
|---|---|---|
| WeasyPrint dependency | adds cairo + pango shared libs to the backend image | gate behind try/except so a runtime where weasyprint can't load falls back to HTML; CI verifies the image still builds with weasyprint present |
| LLM cost (mitigation synthesis) | per-finding LLM call could spike for a 50-finding doc | (1) cache `mitigation_text` per finding; (2) prefer `recommendation`; (3) per-export $0.50 ceiling with 429 response; (4) `/mitigation/synthesize` is an EAGER endpoint operators call once before export, not lazily during render |
| Removing scorecard from on-screen view | downstream dashboards / Spec 006 telemetry consumers may assume the scorecard exists | telemetry stays unchanged (scores stay in the JSON); only the visual component is removed |
| Reference PDF deviation (page-empty when compliant) | conflicts with the literal reference | documented in spec FR-003; pinned by test; clearly tagged as Akhilesh's directive |
| Compliant-summary text generation | requires either a template field on AuditRule (not yet added) OR an LLM call | v1 uses deterministic boilerplate (`"Evaluated across N pages..."`); follow-up enhances rule YAMLs with summary templates |
| Pipeline timing | adding a render step to operator-flow adds 1-2s | acceptable; preview iframe lets the operator decide before committing |

## Implementation phasing

Phases align with the user stories in spec.md.

### Phase 1 — Foundational backend (Story 4 — backwards-compat guard rail)

Build the renderer infrastructure with NO frontend visible. At end of phase: `/export?format=pdf` returns the new PDF; old HTML/MD endpoints unchanged.

1. Add `mitigation_text` field to `ComplianceFinding` (additive, default `""`).
2. Create `backend/app/compliance/report_renderer/` package.
3. Implement `_bucket_status()` with HITL override handling.
4. Implement `_format_pages()`.
5. Implement `_summarise_compliant()` (deterministic boilerplate v1).
6. Implement `_pick_mitigation()` with rec → cache → LLM-synth → boilerplate fallback chain.
7. Implement `build_report_document()` pure transform.
8. Implement `render_html()` via Jinja2 template that matches the reference PDF's CSS layout.
9. Implement `render_pdf()` by routing HTML through weasyprint.
10. Implement `render_md()` for the fallback case.
11. Update `compliance.py` route: rewrite `/export` to consume the new renderer chain.
12. Add `compliance.report_rendered` telemetry.
13. Tests for each module.

**Exit criteria**: PDF download via `/export?format=pdf` matches the reference visually (manual 9-of-10 check). HTML/MD updated. No frontend changes yet.

### Phase 2 — `/report-rows` endpoint (Story 2 — frontend feeds from JSON)

1. Add `/report-rows` route returning the `ReportDocument` shape as JSON.
2. Add the route to `_QUIET_ROUTES`.
3. Tests: shape, agent filter, exclusion of `not_applicable`.

**Exit criteria**: `curl /api/compliance/{doc_id}/report-rows` returns the JSON shape.

### Phase 3 — Frontend rule-centric view (Story 2)

1. Add TS types in `frontend/src/types/compliance.ts` for `ReportRow`, `ReportDocument`.
2. Add API client functions in `frontend/src/lib/api.ts`.
3. Build new components: `RuleTable`, `RuleRow`, `ComplianceBadge`, `RuleExpandDrawer`.
4. Update `compliance-report.tsx` — keep `AgentScorecard` panels and `ExecutiveSummary` AS-IS (score info stays on-screen for internal use); swap the `FindingsTable` block for `RuleTable`; add a Download dropdown that hits `/export?format={pdf|html|md}` and a Preview button that opens the iframe modal.
5. Keep the page route at `frontend/src/app/compliance/page.tsx` intact (scorecard + exec summary section unchanged); just update the body region where findings used to live.
6. Delete `findings-table.tsx`. `agent-scorecard.tsx` and `executive-summary.tsx` STAY.
7. Manual smoke test: load `/compliance?doc=X`, verify scorecard + exec summary render as today, the rule-centric table renders below them, expand drawer works, HITL approve/reject updates the row badge, downloading the PDF shows NO scores in the rendered file.

**Exit criteria**: On-screen view shows score-rich top section + rule-centric table below. Downloaded PDF has no scores. Two surfaces, one data source.

### Phase 4 — Mitigation eager-warm endpoint (Story 3)

1. Add `POST /mitigation/synthesize` route.
2. Implement the iteration: for each finding without `recommendation` and without `mitigation_text`, call the LLM once, persist back to `compliance_result.json` via atomic write.
3. Add `compliance.mitigation_synthesised` telemetry.
4. Enforce per-request cost ceiling: short-circuit at $0.50 with `429`.
5. Tests: cache priority order, force flag, cost ceiling.
6. Frontend: add a "Synthesise mitigation" button on the report page; surfaces a progress toast.

**Exit criteria**: An operator who runs the synthesise endpoint then exports the PDF sees populated Mitigation cells across all non-compliant/uncertain rows.

### Phase 5 — Preview iframe (Story 1, polish)

1. Add `GET /preview` route returning PDF inline.
2. Add the route to `_QUIET_ROUTES`.
3. Build `ReportPreviewIframe` component; surface it as a "Preview" button on the report page that pops a modal with the iframe.
4. Manual test: preview matches the downloadable export 1:1.

**Exit criteria**: Operator can preview before downloading.

### Phase 6 — Cross-cutting polish

1. Add the new endpoints to the OpenAPI schema (if maintained).
2. Update the documentation README (which endpoints are operator-facing, which are downstream-only).
3. End-to-end test: full flow from "compliance run done" → "PDF downloaded" with a fixture doc.

## Risks & open items

- **Reference PDF authority**: if the client revises the reference, this spec must re-anchor. Build a small "reference diff" test (renders a sample report, compares structure against a golden PNG via SSIM). Out of scope for v1.
- **Logo asset**: a generic compliance-suite logo (neutral, non-client-specific) ships under `assets/logo.png`. Asset path is configurable via `AT_COMPLIANCE__REPORT_LOGO_PATH` for any future re-skin.
- **Multi-language**: zero coverage in v1. All text English.
- **A11y**: PDF accessibility tagging — weasyprint supports it; we'll enable it in CSS without measuring against WCAG AA in v1.
- **Score telemetry continuation**: external dashboards reading `ComplianceReport.overall_score` keep working. No deprecation timeline set; revisit if/when those consumers no longer need it.
