# Contract — Stage (v2)

**Spec**: 001-bmr-audit-pipeline  **Revision**: v2 (2026-04-17) — 5 stages, Compliance stage with internal parallel fan-out.

A Stage is a unit of LangGraph orchestration. It consumes the run's state, invokes
capabilities (or delegates to existing agents), decides its gate verdict, and hands off to
the next stage.

---

## 1. ABC

```python
class Stage(Protocol):
    stage_id: PipelineStage
    allows_parallel_scopes: bool      # True → per-document or per-page fan-out allowed
    sub_phases: list[StageSubPhase]   # non-empty only for COMPLIANCE

    async def execute(self, ctx: StageContext) -> StageResult: ...
    async def evaluate_gate(self, ctx: StageContext, result: StageResult) -> GateResult: ...
    async def handle_selective_rerun(self, ctx: StageContext, plan: ReExecutionPlan) -> StageResult: ...
```

## 2. StageContext

- `run_id: UUID`
- `stage: PipelineStage`
- `state: BMRAuditState` — LangGraph state
- `services: StageServices` — ports (capability_registry, finding_store, rule_loader,
  document_store, hitl_port, audit_trail_store)
- `mode: PipelineMode`
- `correlation_id: str`

## 3. StageResult

- `scope_results: list[ScopeResult]` — one per independent scope (document or page)
- `capability_invocations: list[CapabilityInvocation]` — for audit trail
- `gate_inputs: GateInputs` — the data the stage's `evaluate_gate` consumes

## 4. Behavioural Contracts

- **4.1 Gate authority**: Only the stage itself decides its gate. `gate_result = PASS` is
  necessary for the next stage to start. `FAIL` flips the stage to HITL (legibility only) or
  causes a run-level fault and halts pipeline.
- **4.2 HITL restriction**: Mid-pipeline HITL is permitted ONLY in
  `LEGIBILITY_AND_CLASSIFICATION` and ONLY with actions `PAGE_REUPLOADED | PROCEED_ANYWAY`.
  Any stage invoking `hitl_port.interrupt_finding_level()` outside `REPORT_AND_RESOLUTION`
  is a violation.
- **4.3 Capability dispatch**: Stages invoke capabilities via the registry. Direct imports of
  capability classes are forbidden. This enables the re-run planner to substitute scopes.
- **4.4 Idempotency / resumability**: Each stage must be restartable from its checkpoint.
  Scope-level progress (per-document, per-page) must be checkpointed incrementally so a crash
  does not force re-running completed scopes (SC-007).
- **4.5 No direct store writes outside declared ports**: Stages write Findings via
  FindingStore, audit trail via AuditTrailStore, corrections/resolutions via their stores —
  never via raw DB.
- **4.6 Error handling**: Capability-level errors become either (a) a `SYSTEM_ERROR` Finding
  with severity=CRITICAL in scope (if the rule declared a fallback), or (b) a stage-level
  fault that halts the run and produces a `RunFailure` event. Silent drops are a violation
  (Constitution II rule: gate failures produce findings, not silent drops).
- **4.7 Selective re-run entry**: `handle_selective_rerun` accepts a ReExecutionPlan and
  executes only the declared rules/capabilities at declared scopes. It MUST NOT touch scopes
  outside the plan. It MUST preserve `PipelineStageState.status = DIRTY` during execution and
  return to `PASSED`/`FAILED` on completion.

## 5. Per-stage specifics

### 5.1 INGEST

- **Purpose**: Accept the uploaded package (zip / PDF set + optional manifest) and produce a
  normalised set of documents + pages + derived render artifacts. Delegates most work to
  Spec 002.
- **Gate**: `PASS` iff at least one document has been produced AND mandatory roles from the
  manifest (BPCR) are present. Missing anchor → `FAIL` with single `ANCHOR_MISSING` Finding.
- **HITL**: None.
- **Parallel**: No (ingest is batch).

### 5.2 LEGIBILITY_AND_CLASSIFICATION

- **Purpose**: Classify each document against the manifest's declared roles (Spec 002) and
  run `legibility_check.v1` per page. Produce boundary and role assignments.
- **Gate**: `PASS` iff every page has a legibility verdict AND every flagged page has been
  resolved via legibility HITL. Package proceeds per-page: pages with `PASS` or
  `MARGINAL (proceed_anyway)` continue; pages with `FAIL` not yet resolved block only
  themselves.
- **HITL**: Narrow — `PAGE_REUPLOADED | PROCEED_ANYWAY` only.
- **Parallel**: Yes, per page.

### 5.3 STRUCTURED_EXTRACTION_AND_SUMMARISATION

- **Purpose**: Invoke OCR engines + VLM extraction to produce structured fields. Invoke
  `page_summary.v1` (for BPCR pages) and `doc_summary.v1` (for other docs) per the
  configured summary template.
- **Gate**: `PASS` iff every non-failed page has a structured extraction AND summaries for
  required roles have been produced.
- **HITL**: None.
- **Parallel**: Yes, per page for extraction; per doc for summaries.

### 5.4 COMPLIANCE

- **Purpose**: Run all compliance evaluations.
- **Sub-phases**:
  1. `prepared` — rule set loaded + context_objects resolved for each applicable rule.
  2. `alcoa_in_progress` ∥ `gmp_in_progress` — **run in parallel**. Each branch invokes the
     EXISTING agent (`ALCOAComplianceAgent`, `GMPComplianceAgent`) and collects their
     Findings. New rules using `context_object` are routed through the rule engine, which in
     turn calls `page_aggregate_eval.v1` or `cross_doc_rule_eval.v1` as appropriate. Other
     rules continue through the existing evaluator paths.
  3. `alcoa_done` + `gmp_done` → `joined`.
  4. `synthesis_in_progress` — `checklist_synthesise.v1` runs against the joined findings
     set; synthesises where `synthesises_from` is present, falls back to direct evaluation
     otherwise.
  5. `synthesis_done`.
- **Gate**: `PASS` iff all three branches (alcoa, gmp, synthesis) completed without
  stage-level fault. Findings themselves do NOT fail the gate — they are the output.
- **HITL**: None.
- **Parallel**: Yes — ALCOA and GMP branches run concurrently. Within each branch,
  per-scope parallelism is delegated to the agent's existing concurrency model.

### 5.5 REPORT_AND_RESOLUTION

- **Purpose**: Present the consolidated findings view, accept reviewer resolutions
  (`StructuredResolution`), accept corrections (with re-execution plans), and produce the
  export bundle on completion.
- **Gate**: `PASS` iff every Critical and Major Finding has a `StructuredResolution` whose
  `superseded_by` is null (i.e., not made stale).
- **HITL**: Final checkpoint. The full `CONFIRM | DISMISS | CORRECT` action space.
- **Parallel**: N/A (human-driven).
- **Re-run**: On `CORRECT`, the stage:
  1. Generates a `ReExecutionPlan` from the rule engine reverse graph over `context_object`
     dependencies.
  2. Shows the plan to the reviewer for confirmation.
  3. On confirmation, calls `handle_selective_rerun` on the Compliance stage with the plan.
  4. Re-enters `REPORT_AND_RESOLUTION` with updated findings; any superseded resolutions are
     surfaced for re-action.

## 6. State-machine invariants

- Stage order is strict: `INGEST → LEGIBILITY_AND_CLASSIFICATION → STRUCTURED_EXTRACTION_AND_SUMMARISATION → COMPLIANCE → REPORT_AND_RESOLUTION`.
- A stage may enter `DIRTY` state ONLY from `REPORT_AND_RESOLUTION`'s selective re-run path.
  The rerun planner computes which earlier stages need DIRTY-re-entry (usually just
  COMPLIANCE; sometimes STRUCTURED_EXTRACTION_AND_SUMMARISATION if the correction changes an
  extracted field that summaries depend on).
- At most one stage is `IN_PROGRESS` at a time at the stage level; parallel fan-out is
  internal to a single stage (COMPLIANCE sub-phases).

## 7. Testing obligations

- Per-stage contract tests: gate semantics, HITL restriction, restart-from-checkpoint.
- `test_parallel_compliance.py`: asserts ALCOA and GMP execute concurrently and that
  synthesis waits for both.
- `test_legibility_hitl_scope.py`: asserts only `PAGE_REUPLOADED | PROCEED_ANYWAY` actions
  are accepted at this touchpoint.
- `test_selective_rerun.py`: asserts the rerun plan touches only the rules reachable from the
  correction via the `context_object` reverse graph.
- `test_checkpoint_restart.py`: kill the run mid-stage; assert on restart the completed
  scopes are not re-executed.
