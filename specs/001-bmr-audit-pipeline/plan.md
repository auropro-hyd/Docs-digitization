# Implementation Plan: BMR Audit Pipeline

**Branch**: `001-bmr-audit-pipeline` | **Date**: 2026-04-17 | **Revision**: v2 (leverage-first, per Constitution v1.1.0) | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-bmr-audit-pipeline/spec.md`

## Summary

Add a new `bmr_audit` pipeline mode that orchestrates the **existing** compliance framework
(ALCOA agent, GMP agent, rule engine, OCR, VLM, review store) across a 5-stage BMR flow:

`Ingest → Legibility & Classification → Structured Extraction & Summarisation → Compliance (ALCOA ∥ GMP ∥ Checklist-Synthesis) → Report & Resolution`

The rule engine gains a `context_object` field (Spec 005) so rules can declare within-page /
page-aggregate / cross-document evaluation without requiring new code for each behaviour. New
capabilities added: `legibility_check`, `boundary_detect`, `page_summary`, `doc_summary`,
`page_aggregate_eval`, `cross_doc_rule_eval`, `checklist_synthesise`. Existing capabilities
remain: step-level rule eval, signature detect, timestamp check, etc. Old agent entrypoints
(`ALCOAComplianceAgent`, `GMPComplianceAgent`, existing checklist/GMP code paths) are
preserved and invoked from the new orchestrator — they are the backbone (Constitution VII).

The SOP agent is retired at runtime; its rules are extracted offline into ALCOA / GMP rule
banks (Spec 005 authoring skill).

Degraded-mode process-replication is defined but not implemented in v1 unless leverage-mode
results require it.

## Technical Context

**Language/Version**: Python 3.11+ backend (matches `backend/pyproject.toml`); TypeScript 5.x / Node 20+ frontend (Next.js 15.2, React 19.2).
**Primary Dependencies**:
- Backend: FastAPI, LangGraph + `langgraph-checkpoint-postgres`, SQLAlchemy async + asyncpg, pydantic v2, existing `app/compliance/{alcoa,gmp,checklist,evaluator,orchestrator}.py`, existing `app/workflow/{document_graph,compliance_graph}.py` (reused).
- Frontend: Next.js App Router, Zustand, react-pdf, shadcn/ui, Tailwind CSS v4.
**Storage**: Filesystem JSON remains document-of-record. Postgres for orchestration state (checkpointer, new tables for BMR run state, findings, corrections, resolutions, feedback samples).
**Testing**: pytest + pytest-asyncio (backend); Playwright/vitest (frontend) per existing conventions.
**Target Platform**: Linux server, Python 3.11+, Postgres 15+, Node 20+.
**Project Type**: Web application (backend + frontend coexist).
**Performance Goals** (from spec SC-001, SC-003, SC-007):
- End-to-end pilot package audit ≤ 45 minutes including reviewer time.
- Single-value correction re-run ≤ 30 s p95.
- Pipeline restart resumes with zero recomputation of completed scopes.
**Constraints**:
- MUST NOT regress existing single-document pipeline modes (Constitution VII).
- MUST NOT introduce a new monolithic compliance body; all new behaviour is a rule-spec entry or an atomic capability (Constitution III + IX).
- Single final HITL checkpoint; Legibility HITL is narrow (re-upload / proceed-anyway) (Constitution IV).
- Existing ALCOA/GMP/checklist agents are **extended**, not replaced (Constitution VII).
**Scale/Scope**: Pilot package bounded at ~200 pages / ~10 docs. Single reviewer per run in v1.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design (see "Post-Design Constitution Re-Check" below).*

Reference: `.specify/memory/constitution.md` (v1.1.0).

- [x] **I. Leverage-first**: Plan reuses `app/compliance/` ALCOA / GMP agents and `app/compliance/rules/` rule engine. New code is orchestration (new LangGraph composition) + capabilities for genuinely missing behaviours (legibility pre-check, page aggregation, cross-doc rule eval, checklist synthesis, config-driven summaries). No subsystem replacement.
- [x] **II. 5-stage soft gates + parallel compliance**: `Ingest → Legibility&Classification → StructuredExtraction&Summarisation → Compliance(ALCOA∥GMP → ChecklistSynthesis) → Report&Resolution`. Only Legibility has mid-pipeline HITL, and it is narrow (re-upload/proceed).
- [x] **III. Capability-first**: 7 new capabilities, each atomic. Old agents remain callable but are **composed** of capabilities where new behaviour is added; no new logic goes into a monolithic agent.
- [x] **IV. Single final checkpoint & selective re-run**: `Finding.re_run_scope` + `ReExecutionPlanner` derives minimal re-run set from the rule engine's `context_object` reverse-dependency graph. Legibility HITL only offers re-upload / proceed.
- [x] **V. Evidence-bound findings**: Finding schema requires evidence. Synthesised findings require non-empty `source_finding_ids`.
- [x] **VI. Configurable framework**: All client specifics live under `backend/config/bmr/*.yaml` and `backend/config/rules/*.yaml`. Summary templates, classification roles, reconciliation tolerances, rule bindings — all YAML.
- [x] **VII. Existing framework IS the backbone**: `app/compliance/alcoa.py`, `gmp.py`, `checklist.py`, `orchestrator.py`, `evaluator.py` remain the primary logic. New capabilities live in `app/capabilities/` and are invoked by the existing rule engine + the new BMR orchestrator. The SOP agent is the only deletion; its rules migrate to ALCOA/GMP banks offline.
- [x] **VIII. ALCOA+ audit trail**: Reviewer resolutions are structured (not free-text), persisted immutably, and append into the feedback corpus. All writes carry `user_id`, `recorded_at`, and (for numeric) `tolerance_applied`.
- [x] **IX. Rule-as-data**: New compliance behaviours in this plan are introduced via rule-spec entries with `context_object` (Spec 005) wherever possible. The only genuinely-new capabilities are those the rule-spec schema cannot express as pure configuration (legibility-check, boundary-detect, summaries, checklist-synthesis, rule-engine runtime support for `context_object`).

**No violations requiring Complexity Tracking**. One architectural clarification (retained
old agents) is tracked below, not as a violation.

## Project Structure

### Documentation (this feature)

```text
specs/001-bmr-audit-pipeline/
├── spec.md
├── plan.md                             # This file (v2)
├── research.md                         # Phase 0 (v2)
├── data-model.md                       # Phase 1 (v2)
├── quickstart.md                       # Phase 1 (v2)
├── contracts/
│   ├── capability-contract.md          # Capability ABI (extended for context_object)
│   ├── stage-contract.md               # 5 stages with parallel branches inside Compliance
│   ├── rest-api.md                     # HTTP endpoints for BMR audit
│   └── event-contract.md               # WebSocket events
├── checklists/
│   └── requirements.md
└── tasks.md                            # Generated later by /speckit-tasks
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── workflow/
│   │   ├── document_graph.py                 # EXISTING — unchanged
│   │   ├── compliance_graph.py               # EXISTING — unchanged (still used by non-BMR modes)
│   │   ├── nodes.py                          # EXISTING — unchanged
│   │   ├── state.py                          # EXISTING — unchanged
│   │   └── bmr_audit/                        # NEW subpackage
│   │       ├── __init__.py
│   │       ├── graph.py                      # NEW — 5-stage LangGraph composition
│   │       ├── graph_config.py               # NEW — declarative stage wiring
│   │       ├── stages/
│   │       │   ├── ingest.py                 # NEW — delegates to spec 002
│   │       │   ├── legibility_and_classification.py  # NEW — legibility + boundary detect + classify
│   │       │   ├── extraction_and_summarisation.py   # NEW — OCR + structured parse + summary capabilities
│   │       │   ├── compliance.py             # NEW — invokes EXISTING ALCOAAgent, GMPAgent in parallel + ChecklistSynthesise after
│   │       │   └── report_and_resolution.py  # NEW — final HITL + selective re-run entry
│   │       ├── state.py                      # NEW — BMRAuditState TypedDict
│   │       ├── checkpoint.py                 # NEW — stage-boundary checkpoint helper
│   │       └── rerun_planner.py              # NEW — minimal re-execution plan from rule engine reverse deps
│   ├── capabilities/                         # NEW top-level subpackage
│   │   ├── __init__.py
│   │   ├── base.py                           # NEW — Capability ABC, CapabilityContext, FindingDraft
│   │   ├── registry.py                       # NEW — capability discovery + dependency inversion for planner
│   │   ├── legibility_check.py               # NEW — light legibility + confidence scoring
│   │   ├── boundary_detect.py                # NEW — page-X-of-Y header + content fallback (primary in spec 002)
│   │   ├── page_summary.py                   # NEW — config-driven page-level summary (BPCR)
│   │   ├── doc_summary.py                    # NEW — config-driven document-level summary
│   │   ├── page_aggregate_eval.py            # NEW — within-page aggregations (time gaps, sum of raw materials)
│   │   ├── cross_doc_rule_eval.py            # NEW — evaluates rules whose context_object is cross-document
│   │   └── checklist_synthesise.py           # NEW — synthesise from ALCOA/GMP findings per rule's synthesises_from
│   ├── compliance/                           # EXISTING — the backbone
│   │   ├── alcoa.py                          # EXISTING — unchanged; called from bmr_audit.stages.compliance
│   │   ├── gmp.py                            # EXISTING — unchanged; called from bmr_audit.stages.compliance
│   │   ├── checklist.py                      # EXISTING — callable as a fallback for checklist_synthesise
│   │   ├── sop.py                            # EXISTING — NO LONGER invoked at runtime; retained as offline rule-extractor utility (Spec 005)
│   │   ├── evaluator.py                      # EXTENDED — accepts context_object in rule loading (see spec 003 / 005)
│   │   ├── orchestrator.py                   # EXISTING — still drives non-BMR compliance runs
│   │   ├── rules/                            # EXISTING — schema EXTENDED by spec 005
│   │   ├── applicability.py                  # EXISTING
│   │   ├── context_builder.py                # EXTENDED — supports context_object resolution (cross-doc)
│   │   ├── vision_evaluator.py               # EXISTING
│   │   ├── page_image_loader.py              # EXISTING
│   │   ├── segmentation.py                   # EXISTING
│   │   ├── models.py                         # EXTENDED — Finding gains source, source_finding_ids, produced_in_mode
│   │   └── cross_page/                       # EXISTING — reused; integrates with cross_doc_rule_eval
│   ├── core/
│   │   ├── models/
│   │   │   ├── bmr_run.py                    # NEW
│   │   │   ├── structured_resolution.py      # NEW
│   │   │   ├── correction.py                 # NEW
│   │   │   ├── re_execution_plan.py          # NEW
│   │   │   ├── feedback_sample.py            # NEW
│   │   │   └── audit_trail_entry.py          # NEW (or extend existing)
│   │   ├── ports/
│   │   │   ├── bmr_run_store.py              # NEW
│   │   │   ├── correction_store.py           # NEW
│   │   │   ├── resolution_store.py           # NEW
│   │   │   └── feedback_corpus_store.py      # NEW
│   │   └── services/
│   │       └── rerun_planner.py              # NEW (or under bmr_audit/)
│   ├── adapters/
│   │   └── storage/
│   │       ├── postgres_bmr_run.py           # NEW
│   │       ├── postgres_correction.py        # NEW
│   │       ├── postgres_resolution.py        # NEW
│   │       └── postgres_feedback_corpus.py   # NEW
│   ├── api/
│   │   └── routers/
│   │       ├── bmr_audit.py                  # NEW — REST endpoints
│   │       └── ws_bmr_audit.py               # NEW — WebSocket channel
│   └── config/
│       ├── container.py                      # MODIFY — register bmr_audit pipeline mode
│       └── settings.py                       # MODIFY — add BMRAuditConfig
├── config/
│   ├── bmr/
│   │   ├── pilot-manifest.yaml               # NEW (spec 002)
│   │   ├── pilot-summary-templates.yaml      # NEW (page-level BPCR + doc-level others)
│   │   └── report-template.yaml              # NEW (spec 004)
│   └── rules/
│       └── pilot/                            # NEW — client-namespaced rule specs (spec 005)
│           ├── alcoa/
│           ├── gmp/
│           └── checklist/                    # includes synthesises_from rules
└── tests/
    ├── workflow/bmr_audit/
    │   ├── test_graph_end_to_end.py
    │   ├── test_parallel_compliance.py
    │   ├── test_legibility_hitl_scope.py
    │   ├── test_selective_rerun.py
    │   └── test_checkpoint_restart.py
    ├── capabilities/
    │   ├── test_legibility_check.py
    │   ├── test_boundary_detect.py
    │   ├── test_page_summary.py
    │   ├── test_doc_summary.py
    │   ├── test_page_aggregate_eval.py
    │   ├── test_cross_doc_rule_eval.py
    │   └── test_checklist_synthesise.py
    ├── compliance/
    │   └── test_evaluator_context_object.py   # extends existing evaluator
    ├── regression/
    │   └── test_existing_modes_still_pass.py  # Constitution VII gate
    └── performance/
        └── test_rerun_latency.py              # SC-003

frontend/
├── src/
│   ├── app/bmr-audit/
│   │   ├── page.tsx                                  # NEW — landing
│   │   ├── [run_id]/
│   │   │   ├── page.tsx                              # NEW — run overview
│   │   │   ├── legibility/page.tsx                   # NEW — narrow HITL (upload/proceed only)
│   │   │   └── report/page.tsx                       # NEW — consolidated collapsible findings (spec 004)
│   ├── stores/bmr-audit-store.ts                     # NEW
│   ├── components/bmr-audit/
│   │   ├── stage-progress.tsx                        # NEW — 5-stage progress
│   │   ├── legibility-page-action.tsx                # NEW — upload / proceed only
│   │   ├── findings-by-step.tsx                      # NEW — grouped consolidated view
│   │   ├── collapsible-compliance-section.tsx        # NEW
│   │   └── structured-resolution-form.tsx            # NEW (spec 004)
│   └── lib/{api-bmr.ts, ws-bmr.ts}                   # NEW
```

**Structure Decision**: Web-app structure preserved. The BMR audit pipeline is a **new**
LangGraph composition under `backend/app/workflow/bmr_audit/` that orchestrates the
**existing** `app/compliance/` agents (ALCOA, GMP, checklist) plus the **new**
`app/capabilities/` modules. The existing compliance graph and agents are untouched and
remain the primary drivers for non-BMR pipeline modes. The rule engine (`evaluator.py` +
`context_builder.py`) is **extended** (not replaced) to understand the new `context_object`
field (see spec 005) — this is the key leverage point that lets most new compliance behaviour
land as YAML without new code.

## Complexity Tracking

No principle violations. One clarification for reviewer awareness:

| Item | Why Needed | Simpler Alternative Considered |
|---|---|---|
| Retaining the SOP agent file as an offline rule-extractor utility while deleting its runtime path | Constitution VII prefers extension to deletion. The SOP file contains domain logic that needs to migrate into ALCOA/GMP rules gradually via Spec 005's authoring skill. Deleting the file immediately would lose that knowledge. | Delete immediately. Rejected: would discard extraction logic before migration is complete. |
| Keeping `app/workflow/compliance_graph.py` intact while BMR uses a different graph | Non-BMR modes still invoke the old compliance graph. Refactoring them together would widen blast radius unnecessarily. | Unify both graphs. Rejected: expands BMR schedule risk into non-BMR modes. |

## Post-Design Constitution Re-Check

*Filled in after Phase 1 (data-model.md, contracts/, quickstart.md) is complete.*

- [x] **I. Leverage-first**: `contracts/stage-contract.md §5.4 Compliance` explicitly invokes the existing `ALCOAComplianceAgent` and `GMPComplianceAgent` rather than reimplementing. Checklist-Synthesis reads ALCOA/GMP output before falling back to direct eval.
- [x] **II. 5 stages + parallel compliance**: `contracts/stage-contract.md` declares exactly 5 stages. The Compliance stage declares an internal fan-out to ALCOA and GMP branches that converge before Checklist-Synthesis.
- [x] **III. Capability-first**: `contracts/capability-contract.md` unchanged from v1 except for the new `context_object` input-spec; no branch is added inside any monolithic agent.
- [x] **IV. Single final + selective re-run**: Legibility HITL schema in `contracts/rest-api.md §2.3` offers only `page_reuploaded` or `proceed_anyway` + optional note — no finding-level controls. Final checkpoint + rerun planner in `§2.6–2.9` unchanged.
- [x] **V. Evidence-bound**: `data-model.md §1.3` Finding schema includes `source` and `source_finding_ids` for synthesised findings; validation rejects synthesised findings with empty sources.
- [x] **VI. Configurable framework**: `data-model.md §6` pins all client-specific artifacts to `backend/config/bmr/` and `backend/config/rules/`.
- [x] **VII. Existing framework backbone**: `plan.md §Project Structure` leaves `app/compliance/*.py` and `app/workflow/document_graph.py`, `compliance_graph.py` unchanged. The only extensions are additive (context_object support in `evaluator.py` and `context_builder.py`).
- [x] **VIII. ALCOA+ + structured resolution**: `data-model.md §1.5 StructuredResolution` enforces `reason_type` + `observed_value` + `system_extracted_value`. Free-text-only resolutions are schema-rejected.
- [x] **IX. Rule-as-data**: `contracts/capability-contract.md §5` lists `context_object` as first-class InputSpec. `plan.md §Summary` confirms each new cross-doc / page-aggregate behaviour lands as a rule entry before any new capability.

Verdict: all 9 gates pass after Phase 1 design.
