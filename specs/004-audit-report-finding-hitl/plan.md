# Implementation Plan: BMR Audit Report & Finding-Level HITL

**Branch**: `004-audit-report-finding-hitl` | **Date**: 2026-04-17 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/004-audit-report-finding-hitl/spec.md`

## Summary

Build the **single final checkpoint** of the BMR audit pipeline: a consolidated report, a
structured-resolution workflow (CONFIRM / DISMISS / CORRECT), an evidence-linked viewer, a
PDF + JSON export, and a feedback corpus that accumulates every resolution as training
data. This is the only place where findings themselves are acted on — mid-pipeline HITL
(legibility re-upload / proceed only) is owned by Spec 001.

No new rule engine, no new OCR, no new pipeline stages. The backend reuses Spec 001's
`Run`, `Finding`, `StructuredResolution`, `FeedbackSample` entities (extends them with
report-specific projections) and the existing selective-rerun planner (Spec 003's reverse
graph).

## Technical Context

**Language/Version**: Python 3.11+ backend; TypeScript 5.x / Node 20+ frontend.
**Primary Dependencies**:
- Backend: FastAPI, existing finding/run stores, WeasyPrint or ReportLab for PDF export
  (decision in research §R-3), pydantic v2 for bundle schema, existing WebSocket broadcaster.
- Frontend: Next.js 15, React 19, TypeScript, Tailwind CSS v4, shadcn/ui, Zustand, PDF.js
  for page rendering, existing annotation/highlight components.
**Storage**: Postgres (reusing Spec 001 schema); `audit_report_revision`,
`feedback_sample`, `structured_resolution`, `correction_workflow` tables.
**Testing**: pytest + pytest-asyncio (backend); Playwright component + e2e (frontend).
**Target Platform**: Web app.
**Performance Goals** (from spec SC-005 / SC-009 / SC-010 / SC-011):
- Evidence viewer opens to correct page + highlight in ≤ 1.5 s p95.
- Structured resolution form submit ≤ 500 ms p95.
- PDF export for a 200-finding run ≤ 15 s p95.
- Selective re-run after correction ≤ 30 s p95 (cross-check with Spec 001 SC-003).
**Constraints**:
- Single final checkpoint (Constitution IV) — no finding-level actions elsewhere.
- No overall "compliance score" anywhere in the UI or export (SC-004 + BUC §16).
- Every DISMISS and CORRECT MUST produce a `StructuredResolution` with `reason_type` and
  `observed_value_on_document` (for OCR_MISREAD / ACCEPTABLE_VARIANCE). Free-text only is
  rejected.
- Every `StructuredResolution` MUST seed one `FeedbackSample` (Constitution IX).
**Scale/Scope**: Up to 500 findings per run. Up to 50 concurrent reviewers across runs.
Corrections trigger Spec 003-indexed re-run only.

## Constitution Check

Reference: `.specify/memory/constitution.md` (v1.1.0).

- [x] **I. Leverage-first**: Reuses existing finding model, viewer primitives, PDF
  rendering, WebSocket infra. New code: report projections, resolution form schema,
  export engine, feedback corpus seeder.
- [x] **II. 5-stage soft gates**: Sits in the `REPORT_AND_RESOLUTION` stage (Stage 5).
  Does not touch earlier stages.
- [x] **III. Capability-first**: `report_project.v1` (projection + grouping),
  `report_export.v1` (PDF + JSON), `feedback_seed.v1` (one StructuredResolution → one
  FeedbackSample) are new atomic capabilities.
- [x] **IV. Single final checkpoint**: This spec IS the checkpoint. It defines the
  vocabulary (CONFIRM / DISMISS / CORRECT), the reason types, and the gating (cannot export
  until all Critical/Major actioned).
- [x] **V. Evidence-bound findings**: Viewer is evidence-first. Dismiss with
  `reason_type=OCR_MISREAD` requires `observed_value_on_document` — i.e., the reviewer
  read the evidence.
- [x] **VI. Configurable framework**: Export template sections (BUC §16 layout), dismiss
  reason types, severity escalation rules are configuration
  (`config/bmr/report-*.yaml`).
- [x] **VII. Existing framework backbone**: Existing finding model, run store,
  WebSocket streams, viewer components are reused. New tables sit alongside, not replace.
- [x] **VIII. ALCOA+ audit trail**: `StructuredResolution`, `CorrectionWorkflow`,
  `AuditReportRevision` are all append-only with actor + server-assigned timestamp.
  Superseded-by chains for re-run invalidation.
- [x] **IX. Rule-as-data**: Reason types, severity gating, export sections are YAML. No
  pilot-specific enum values hardcoded in Python beyond the fixed action + reason-type
  enums.

No violations.

## Project Structure

```text
specs/004-audit-report-finding-hitl/
├── spec.md
├── plan.md                       # this
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── rest-api.md
│   ├── event-contract.md
│   └── capability-contract.md
└── checklists/requirements.md
```

```text
backend/
├── app/
│   ├── report/                                 # NEW subpackage
│   │   ├── __init__.py
│   │   ├── projection.py                       # grouping: step → ALCOA|GMP|Checklist
│   │   ├── exporter_pdf.py                     # BUC §16 layout
│   │   ├── exporter_bundle.py                  # structured JSON
│   │   ├── gating.py                           # enforce Critical/Major actioned
│   │   └── revision.py                         # report revision chaining
│   ├── resolution/                             # NEW subpackage
│   │   ├── __init__.py
│   │   ├── schema.py                           # reason_type + value capture
│   │   ├── validator.py                        # rejects free-text-only
│   │   ├── apply.py                            # persist + seed feedback
│   │   └── correction_workflow.py              # CORRECT action end-to-end
│   ├── feedback/                               # NEW subpackage
│   │   ├── __init__.py
│   │   ├── seeder.py                           # 1 resolution → 1 FeedbackSample
│   │   └── queries.py                          # corpus read API for spec 005 skill
│   ├── capabilities/
│   │   ├── report_project.v1.py                # NEW
│   │   ├── report_export.v1.py                 # NEW
│   │   └── feedback_seed.v1.py                 # NEW
│   ├── core/models/
│   │   ├── audit_report_revision.py            # NEW
│   │   ├── correction_workflow.py              # NEW
│   │   ├── report_section.py                   # NEW (projection view model)
│   │   └── (StructuredResolution, FeedbackSample are Spec 001 entities — reused)
│   └── api/routers/
│       ├── reports.py                          # NEW
│       ├── resolutions.py                      # NEW
│       └── corrections.py                      # NEW (backs the Correct action)
├── config/bmr/
│   ├── report-sections.yaml                    # NEW (BUC §16 layout)
│   ├── report-severity-gating.yaml             # NEW
│   └── resolution-reason-types.yaml            # NEW
└── tests/
    ├── report/
    │   ├── test_projection_grouping.py
    │   ├── test_severity_gating.py
    │   ├── test_export_pdf_layout.py
    │   ├── test_export_bundle_schema.py
    │   └── test_revision_chaining.py
    ├── resolution/
    │   ├── test_schema_rejects_free_text_only.py
    │   ├── test_reason_type_requires_observed_value.py
    │   ├── test_dismiss_confirm_correct.py
    │   └── test_feedback_seeding.py
    ├── correction/
    │   ├── test_correction_triggers_rerun.py
    │   ├── test_stale_confirmation_reappears.py
    │   └── test_legibility_re_entry.py
    └── fixtures/report/
        ├── run_with_200_findings.json
        └── severity_gating.yaml

frontend/
├── app/bmr/runs/[runId]/report/
│   ├── page.tsx                                # consolidated grouped view
│   ├── flat-by-severity.tsx                    # toggle view
│   └── export-blocked-dialog.tsx
├── components/report/
│   ├── StepGroup.tsx                           # collapsible BPCR step group
│   ├── ComplianceSubSection.tsx                # ALCOA | GMP | Checklist
│   ├── FindingCard.tsx                         # status + severity + actions
│   ├── EvidenceViewer.tsx                      # page render + highlight
│   ├── EvidenceSourceSwitcher.tsx              # cross-doc navigation
│   ├── ResolutionForm.tsx                      # reason_type + fields per type
│   ├── CorrectionForm.tsx                      # value edit + rerun preview
│   ├── ReRunPreview.tsx                        # which rules will re-run
│   ├── RetractionMarker.tsx                    # stale/re-review markers
│   └── NeedsReActionTray.tsx                   # superseded resolutions
├── lib/report/
│   ├── api.ts
│   └── store.ts                                # Zustand report state
└── tests/e2e/
    ├── happy-path-export.spec.ts
    ├── dismiss-requires-reason-type.spec.ts
    ├── correct-triggers-rerun.spec.ts
    └── evidence-viewer-cross-doc.spec.ts
```

**Structure Decision**: Web app, backend extends per-run finding store with
report-centric subpackages; frontend adds a dedicated `/report/` feature under
`app/bmr/runs/[runId]/`. No existing endpoints changed; new endpoints added under
`/reports`, `/resolutions`, `/corrections`.

## Complexity Tracking

| Item | Why | Simpler Alternative Considered |
|---|---|---|
| Separate `exporter_pdf` and `exporter_bundle` | BUC §16 requires a stakeholder-visible PDF AND a machine-readable bundle for downstream systems; different rendering engines, same projection. | One exporter producing both. Rejected: PDF layout logic and JSON schema logic pollute each other and fail separation of concerns. |
| Report revisions instead of overwrites | A signed export is a deliverable; subsequent edits MUST produce a new, linked revision rather than mutate history (Constitution VIII). | In-place edits with a history table. Rejected: semantics are identical but the signed-deliverable mental model is clearer with explicit revisions. |

## Post-Design Constitution Re-Check

- [x] **I**: Existing infra reused; only report/resolution/feedback subpackages new.
- [x] **II**: Stage 5 only.
- [x] **III**: Three atomic capabilities (`report_project.v1`, `report_export.v1`,
  `feedback_seed.v1`).
- [x] **IV**: Only this spec's endpoints mutate finding state. Mid-pipeline paths emit no
  resolution-shaped events.
- [x] **V**: Dismiss schema enforces `observed_value_on_document` for value-dependent
  reason types (test: `test_reason_type_requires_observed_value.py`).
- [x] **VI**: `report-sections.yaml`, `resolution-reason-types.yaml`,
  `report-severity-gating.yaml` own the pilot layout.
- [x] **VII**: No changes to existing finding API shape.
- [x] **VIII**: Append-only `audit_report_revision`, `correction_workflow`, and
  supersedes chains on `structured_resolution`.
- [x] **IX**: Reason types and gating are YAML-loaded.

All 9 gates green after Phase 1.
