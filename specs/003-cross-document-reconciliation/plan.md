# Implementation Plan: Cross-Document Rule Support

**Branch**: `003-cross-document-reconciliation` | **Date**: 2026-04-17 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/003-cross-document-reconciliation/spec.md`

## Summary

Extend the existing rule engine with a declarative `context_object` block so a single YAML
rule can reach into other documents in a BMR package, or aggregate across pages within a
document, without any new subsystem. Two new atomic capabilities
(`cross_doc_rule_eval.v1`, `page_aggregate_eval.v1`) provide the execution surface. Entity
matching is configuration (strategies + normalisation + aliases), not a new code module.

This plan is strictly additive. Rules without `context_object` (the entire legacy rule bank)
run through the existing same-page evaluator unchanged.

## Technical Context

**Language/Version**: Python 3.11+.
**Primary Dependencies**: existing rule engine (`backend/app/rules/`), existing finding model,
pydantic v2 for rule loader validation, YAML loader. VLM/OCR NOT needed — this layer
consumes already-extracted structured fields + summaries produced by Spec 001/002.
**Storage**: Read-only against already-persisted `Summary`, `DocumentRef`,
`ClassificationResult`. Writes `Finding`, `ResolvedContext` audit-log rows via existing
stores.
**Testing**: pytest + pytest-asyncio. Fixture rule banks under `backend/tests/fixtures/rules/`.
**Target Platform**: same runtime as the rest of the backend.
**Project Type**: backend library extension (no frontend changes in this spec; finding-detail
UI changes are owned by Spec 004).
**Performance Goals** (from spec SC-004 and Spec 001 SC-003):
- A single cross-doc rule evaluation (including entity match + tolerance) completes in
  ≤ 50 ms p95 for pilot-sized documents.
- Selective re-run after a one-value correction completes in ≤ 30 s p95.
**Constraints**:
- No client-specific match logic in Python (Constitution VI / IX). All strategies are enum
  values + YAML knobs.
- Legacy same-page evaluator path MUST NOT regress (Constitution VII).
- Implicit equality on numeric fields is forbidden (FR-006 / Constitution VIII — Accurate).
**Scale/Scope**: Pilot rule bank ~150 rules, ~30% cross-doc, ~10% page-aggregate. Packages
up to 25 docs / 500 pages.

## Constitution Check

Reference: `.specify/memory/constitution.md` (v1.1.0).

- [x] **I. Leverage-first**: Reuses the existing rule engine, finding model, evidence
  model, rule loader, reverse-dependency graph. New code: two capabilities + rule-loader
  extension.
- [x] **II. 5-stage soft gates**: Execution sits inside the `COMPLIANCE` stage (ALCOA and
  GMP branches). Does not introduce new stages.
- [x] **III. Capability-first**: `cross_doc_rule_eval.v1` and `page_aggregate_eval.v1` are
  atomic; contracts defined in `contracts/capability-contract.md`.
- [x] **IV. Single final checkpoint**: No HITL introduced. Findings produced here flow into
  Spec 004's final-checkpoint UI like any other finding.
- [x] **V. Evidence-bound**: FR-005 — every cross-doc finding MUST attach evidence from
  every participating document. Bug-level enforcement in capability implementations.
- [x] **VI. Configurable framework**: Entity strategies, normalisation knobs, aliases,
  tolerance, multiplicity, fallback — all YAML. No pilot semantics in Python.
- [x] **VII. Existing framework backbone**: Rules without `context_object` continue to run
  through the legacy evaluator. Regression test
  `tests/regression/test_legacy_same_page_rules_unchanged.py` is a required CI gate.
- [x] **VIII. ALCOA+ audit trail**: `ResolvedContext` rows capture how the rule reached its
  verdict (which counterpart document, which match key, which tolerance).
- [x] **IX. Rule-as-data**: The whole point of this feature. `context_object` is a schema
  field, defined by Spec 005.

No violations.

## Project Structure

```text
specs/003-cross-document-reconciliation/
├── spec.md
├── plan.md                        # this
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── capability-contract.md
│   ├── rule-loader-contract.md
│   └── evaluator-integration.md
└── checklists/requirements.md
```

```text
backend/
├── app/
│   ├── rules/
│   │   ├── engine.py                              # EXISTING — extended
│   │   ├── loader.py                              # EXISTING — extended: parse context_object
│   │   ├── context_resolver.py                    # NEW: resolves same_page | cross_document | page_aggregate
│   │   ├── entity_match/                          # NEW subpackage
│   │   │   ├── __init__.py
│   │   │   ├── strategies.py                      # enum + pure-function implementations
│   │   │   ├── normaliser.py                      # case / whitespace / punctuation
│   │   │   └── aliases.py                         # YAML loader + lookup
│   │   ├── tolerance.py                           # EXISTING — keep, extend for percent-zero guard
│   │   ├── multiplicity.py                        # NEW: first|all|error policies
│   │   ├── fallback.py                            # NEW: flag_as_unevaluated|indeterminate|treat_as_pass
│   │   └── reverse_graph.py                       # EXISTING — extended to index context_object
│   ├── capabilities/
│   │   ├── cross_doc_rule_eval.v1.py              # NEW
│   │   └── page_aggregate_eval.v1.py              # NEW
│   ├── core/models/
│   │   ├── resolved_context.py                    # NEW (ephemeral + audit-log persisted)
│   │   └── alias_table.py                         # NEW (loaded from YAML)
│   └── api/routers/
│       └── (no new endpoints in this spec)
├── config/rules/pilot/
│   ├── alcoa/                                     # EXISTING
│   ├── gmp/                                       # EXISTING
│   └── aliases/                                   # NEW
│       ├── materials.yaml
│       ├── equipment.yaml
│       └── step-names.yaml
└── tests/
    ├── rules/
    │   ├── test_context_resolver.py
    │   ├── test_entity_match_strategies.py
    │   ├── test_aliases_loader.py
    │   ├── test_multiplicity_policies.py
    │   ├── test_fallback_policies.py
    │   └── test_reverse_graph_indexing.py
    ├── capabilities/
    │   ├── test_cross_doc_rule_eval.py
    │   └── test_page_aggregate_eval.py
    ├── regression/
    │   └── test_legacy_same_page_rules_unchanged.py
    └── fixtures/rules/
        ├── cross_doc/quantity_mismatch.yaml
        ├── cross_doc/checklist_signature_step3.yaml
        ├── page_aggregate/bpcr_sum_vs_batch_total.yaml
        └── aliases/materials.yaml
```

**Structure Decision**: This is a backend-library-only extension — no frontend, no new API.
All changes are inside `backend/app/rules/` + two new capabilities. Config lives under
`backend/config/rules/pilot/aliases/`.

## Complexity Tracking

| Item | Why | Simpler Alternative Considered |
|---|---|---|
| Two evaluator capabilities instead of one | `cross_document` and `page_aggregate` resolve inputs from distinct scopes; mixing their resolution logic into one capability would grow a branching tree that becomes hard to reason about and test. Separate capabilities keep each under ~150 LOC with a single responsibility. | One `rule_eval.v2` capability with `scope`-based branching. Rejected: muddies capability semantics and violates Constitution III atomicity. |
| Entity-match strategies as an enum with pure-function backends, not a plugin registry | Pilot needs five deterministic strategies; a plugin registry is premature generality and leaks policy into code. | Plugin registry with a `CustomStrategy` port. Deferred to post-v1; the enum reserves a `custom` slot for that future. |

## Post-Design Constitution Re-Check

- [x] **I**: No subsystem replaced; rule engine extended.
- [x] **II**: Lives in COMPLIANCE stage only.
- [x] **III**: Two atomic capabilities, ABC-conformant.
- [x] **IV**: No HITL introduced.
- [x] **V**: `cross_doc_rule_eval.v1` invariant enforces evidence attribution from every
  participating document (contract + unit test).
- [x] **VI**: No hardcoded client logic. `aliases/materials.yaml` is the only place domain
  names live.
- [x] **VII**: `test_legacy_same_page_rules_unchanged.py` asserts parity on the existing
  rule bank.
- [x] **VIII**: `ResolvedContext` persisted for every cross-doc / page-aggregate finding;
  fields match `data-model.md §2.1`.
- [x] **IX**: Rule YAML shape owned by Spec 005; this spec consumes it.

All 9 gates green after Phase 1.
