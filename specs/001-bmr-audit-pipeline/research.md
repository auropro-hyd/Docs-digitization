# Phase 0 — Research: BMR Audit Pipeline (v2, leverage-first)

**Spec**: [spec.md](./spec.md)
**Plan**: [plan.md](./plan.md)
**Revision**: v2 (2026-04-17) — replaces the prior 7-stage research.

This document records the key design decisions taken before data-model and contract work.
Each item is stated as **Decision / Rationale / Alternatives considered**.

---

## 1. Orchestration: new LangGraph composition, existing agents as nodes

**Decision**: Add a new LangGraph composition at `backend/app/workflow/bmr_audit/graph.py`
with 5 stages. Inside the Compliance stage, fan out to two parallel branches (ALCOA and GMP)
that invoke the **existing** `ALCOAComplianceAgent` and `GMPComplianceAgent`; after both
converge, run a Checklist-Synthesis node that reads the produced findings and resolves the
checklist rule bank.

**Rationale**: The existing agents already encapsulate a large body of tested logic, are
integrated with the rule engine, and produce findings in a schema the review store already
understands. The BMR-specific orchestration — multi-document ingest, legibility gating,
cross-document rule evaluation, checklist synthesis — is what is genuinely new. Building only
the new parts sharply bounds the delivery risk for the 3–4 week timeline (per spec
Assumption 7).

**Alternatives considered**:
- **Refactor ALCOA/GMP agents into the new capability model at the same time** — rejected:
  simultaneous refactor + new feature work doubles regression surface and threatens the pilot
  demo schedule. Capability extraction from the existing agents can happen incrementally
  behind this pipeline without breaking the contract.
- **Subclass the existing compliance graph** — rejected: the 5-stage BMR flow differs
  materially (parallel compliance, synthesis, legibility gate, consolidated resolution), and
  inheritance would couple two graphs that should evolve independently.

---

## 2. Rule engine extension for `context_object`

**Decision**: Extend `backend/app/compliance/evaluator.py` and `context_builder.py` to
recognise a new `context_object` block on each rule. Resolution semantics:
- `scope: same_page` → existing behaviour.
- `scope: page_aggregate` with `aggregation: [sum | min | max | sequence]` across
  `applicable_pages` of the same document → new capability `page_aggregate_eval`.
- `scope: cross_document` with `role: <RoleName>` and `entity_match: <strategy>` → new
  capability `cross_doc_rule_eval` that looks up documents by role in the package manifest
  and applies the named entity-matching strategy.

**Rationale**: This is the single highest-leverage change in the programme. It converts a
large family of "write a new Python branch for each cross-doc check" tasks into "write a YAML
rule with the right `context_object`". It is also the operation Akhilesh specifically called
out as the rule engine's central extension.

**Alternatives considered**:
- **Dedicated reconciliation engine as a separate subsystem** (prior spec-003 direction) —
  rejected: separates responsibility unnecessarily and duplicates rule-loading / evidence
  plumbing. Spec 003 is now reframed as "Cross-Document Rule Support" — config for the rule
  engine, not a new engine.
- **Hardcode common cross-doc checks in capabilities with rule bindings pointing at them** —
  rejected: every new cross-doc pattern would still require a new capability. `context_object`
  makes the engine, not the caller, own the lookup logic.

---

## 3. Parallelism inside the Compliance stage

**Decision**: Compliance stage uses LangGraph fan-out: a `prepare_compliance` node emits two
`Send`s, one to each of `alcoa_branch` and `gmp_branch`, which run in parallel. Both merge
at a `compliance_joined` node. Then `checklist_synthesise_node` runs on the joined state.

**Rationale**: The existing agents are independent by construction (different rule banks,
different ALCOA/GMP taxonomies), so parallelism is safe. Serialising them only inflates
latency and wastes CPU / LLM concurrency budget. Checklist synthesis must wait because it
depends on both branches' outputs.

**Alternatives considered**:
- **Run all three sequentially** (as in the prior 7-stage design) — rejected: unnecessary
  latency; doesn't match the agents' actual dependency structure.
- **Fully parallel including Checklist-Synthesis, race it against ALCOA/GMP** — rejected:
  synthesis reads ALCOA/GMP finding ids; no way to do that before they emit.

---

## 4. Legibility gate: narrow HITL, fine scope

**Decision**: Legibility is a per-page verdict. Pages with `PASS` / `MARGINAL` continue
downstream; pages with `FAIL` enter a per-page HITL queue whose only actions are
`page_reuploaded` (with replacement file) and `proceed_anyway` (with optional note). No
finding-level UI. Downstream compliance runs per-page, so a single flagged page does not
block siblings.

**Rationale**: Constitution IV explicitly permits only legibility HITL mid-pipeline, and
explicitly forbids finding-level review there. A narrow action set preserves the
single-final-checkpoint discipline while still handling the dominant real-world failure
(unreadable scans).

**Alternatives considered**:
- **Package-level legibility gate** (flag the whole package, block everything) — rejected:
  one blurry page should not halt the other 199. Per-page gate keeps the pipeline useful.
- **Finding-level HITL in the gate** — explicitly forbidden by Constitution IV.

---

## 5. Selective re-run planner: reverse dependency over `context_object`

**Decision**: The re-execution planner treats the rule engine's `context_object` declarations
as the dependency graph: for an input `I` (document `D`, page `P`, field `F`), the planner
returns the set of rules whose `context_object` names `F` directly or transitively (through
page-aggregates or cross-doc roles bound to the same `D`). Capabilities whose outputs feed
those rules are re-invoked. No others.

The plan is presented to the reviewer as a count + list of scopes before execution.

**Rationale**: This is how "correct one value, re-run only the 3 rules that read it" actually
works. Computing the reverse graph from `context_object` gives a precise, auditable re-run
plan — the reviewer can see why each rule is re-running.

**Alternatives considered**:
- **Re-run the entire Compliance stage** — rejected: violates Constitution IV and
  SC-003 (30 s p95 unattainable on full stage).
- **Re-run at stage granularity using LangGraph checkpoint replay** — rejected: too coarse;
  still re-runs all compliance rules regardless of whether they depend on the correction.

---

## 6. Checklist-Synthesis rule pattern

**Decision**: Checklist rules may carry a `synthesises_from: [rule_id, ...]` field. The
`checklist_synthesise` capability:
1. Reads the findings produced by listed source rules.
2. Applies the synthesis recipe from the rule (default: "all sources passing → checklist
   passes; any source failing → checklist fails, citing the failed source").
3. Produces a Finding with `source: synthesised` and `source_finding_ids = [...]`.
4. Falls back to direct capability evaluation (OCR / summary) **only** when `synthesises_from`
   is absent or all listed rules were `SKIPPED`.

**Rationale**: Akhilesh's directive: don't re-extract facts that ALCOA/GMP already
established. Synthesis also gives the retraction semantics we need — retracting a source
finding automatically retracts its synthesised descendants.

**Alternatives considered**:
- **Always run direct evaluation for checklists** — rejected: duplicates effort and produces
  two finding trees for the same evidence.
- **Implicit synthesis** (infer sources by ALCOA principle match) — rejected: non-deterministic,
  hard to audit.

---

## 7. Structured reviewer resolution

**Decision**: Every `Dismiss` or `Correct` action requires a `StructuredResolution` with:
`reason_type ∈ { OCR_MISREAD, ACCEPTABLE_VARIANCE, DUPLICATE_FINDING, OUT_OF_SCOPE,
RULE_MISCONFIGURED, OTHER }`, `observed_value_on_document` (string), `system_extracted_value`
(string, snapshot of original), and optional `note`. `Confirm` requires only optional note.

**Rationale**: Free-text resolutions produce nothing you can aggregate. Structured resolutions
double as the feedback corpus for rule-spec tuning and OCR fine-tuning (Constitution IX + Spec
004 + Spec 005). `reason_type` is the single most important field for separating rule bugs
from OCR bugs from acceptable variances.

**Alternatives considered**:
- **Free text + LLM classification later** — rejected: latent and lossy.
- **Heavier schema** (full corrective-action workflow) — rejected: premature; the MVP value
  is in `reason_type` and observed-vs-extracted.

---

## 8. Evidence region representation

**Decision**: Evidence stores document_id + page_number + optional region, where region is
one of: `{ bounding_box: [x, y, w, h], units: "normalised"|"pixels" }` OR
`{ text_span: { start: int, end: int, source: "ocr_text" } }`. Both forms are permitted on the
same Finding (e.g., OCR text span + its bounding box). The UI can navigate to either.

**Rationale**: OCR engines in use (Azure DI, Marker, Docling, Data Lab) all return either
boxes or spans. Normalising to a unified schema downstream keeps the UI generic.

**Alternatives considered**:
- **Force bounding-box only** — rejected: text-span OCR engines lose fidelity.
- **Store raw engine output** — rejected: the UI would need to know every engine's schema.

---

## 9. Backward compatibility: zero blast-radius migration

**Decision**: Non-BMR pipeline modes keep using the existing `document_graph.py` /
`compliance_graph.py` untouched. The BMR flow adds a new mode entry in `config/container.py`
and a new LangGraph; the old modes' entry points are unchanged. `evaluator.py` is extended
(new optional field `context_object`) without changing its existing signatures — legacy rules
without the field continue to behave exactly as before.

**Rationale**: Constitution VII. Existing demo paths and single-document flows ship the
product today; they must not break while the BMR mode lands.

**Alternatives considered**:
- **Unify compliance graphs now** — rejected (see §1); deferred to a post-pilot refactor.
- **Require all rules to migrate to `context_object`** — rejected: would force a churn wave
  across every existing rule file for no user-visible benefit.

---

## 10. Degraded-mode (process-replication) fallback

**Decision**: Defined in the spec (US-6, FR-016) as an operator-triggered opt-in that swaps
the orchestration for a sequential BPCR-step-walk. **Not implemented in v1** unless
leverage-mode measurements (SC-002, SC-008) show it is needed. If implemented, it lives in
`backend/app/workflow/bmr_audit/degraded_mode/` and shares the capability registry, findings
schema, and rerun planner.

**Rationale**: Premature optimisation of an escape hatch that may never be used. Keep the
option open in the data model (`produced_in_mode` tag on Finding) but don't spend scarce
weeks building the fallback during the leverage-mode build.

**Alternatives considered**:
- **Build both modes in v1** — rejected: doubles scope.
- **Drop degraded mode entirely** — rejected: Akhilesh explicitly retained it as a fallback;
  the data-model tag is free.

---

## 11. Test strategy

**Decision**: Three test tiers.
1. **Capability unit tests** (`backend/tests/capabilities/`) — each capability in isolation
   with mocked ports.
2. **BMR integration tests** (`backend/tests/workflow/bmr_audit/`) — end-to-end on a fixture
   pilot package, testing: full happy path; parallel-compliance invariants; legibility HITL
   scoping; selective re-run (SC-003); checkpoint/restart (SC-007).
3. **Regression tests** (`backend/tests/regression/test_existing_modes_still_pass.py`) —
   executes `accuracy | quality | reasoning | byok | production` modes on an existing fixture
   and asserts no regressions. **Gate**: this file is a required CI pass for any BMR-touching
   PR (Constitution VII).

A performance test (`backend/tests/performance/test_rerun_latency.py`) pins SC-003 and is
rerun weekly.

**Rationale**: Capability-granular tests give fast feedback without running the whole graph;
integration tests pin the end-to-end guarantees the product promises; regression tests are
the contract with existing customers.

**Alternatives considered**:
- **End-to-end tests only** — rejected: slow, high false-positive rate, hard to localise
  failures.
- **Unit tests only** — rejected: cannot cover stage-parallelism or checkpoint/restart
  invariants.

---

## Deferred questions (not needed before data-model / contracts)

- Exact metrics schema for OCR quality per capability — deferred to spec 004's FeedbackSample
  work.
- Whether rule bank hot-reload belongs in v1 — deferred; v1 loads rules at pipeline start and
  hashes them into the run's audit trail.
- Multi-reviewer collaboration UI — explicitly deferred to post-v1 (spec Assumption 4).
