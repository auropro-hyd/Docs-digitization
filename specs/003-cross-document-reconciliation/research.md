# Research: Cross-Document Rule Support

**Feature**: 003 | **Spec Version**: v2

## R-1. Context-object as runtime contract, schema owned elsewhere

**Decision**: Spec 003 owns the *runtime semantics* of `context_object`; Spec 005 owns the
*schema* (JSON Schema + validation). At service start, the rule loader runs the Spec 005
validator on every rule, then this spec's `context_resolver` binds the validated declaration
to a resolution strategy.

**Why**: Keeps the schema single-sourced (authoring tool + runtime both read one schema) and
prevents schema drift between the two specs. Separation of concerns: schema is declarative
data; resolver is behaviour.

**Alternatives rejected**: Duplicate schema in both specs (drift risk); merge specs 003+005
(couples runtime to authoring tooling, bloats scope).

## R-2. Three resolution modes, one dispatcher

**Decision**: `context_resolver.resolve(rule, run_ctx)` returns a `ResolvedContext` with
shape per `scope`:

- `same_page` — existing evaluator path; the resolver returns a sentinel and the engine
  continues to call the legacy evaluator.
- `cross_document` — resolver (a) looks up documents by `role` in the package manifest,
  (b) applies entity matching, (c) applies `multiplicity` policy, (d) returns matched
  document id + match key + participating fields.
- `page_aggregate` — resolver applies the `page_selector`, reads extracted fields +
  summaries across selected pages, returns the aggregate value + list of participating
  pages.

The engine then invokes `cross_doc_rule_eval.v1` or `page_aggregate_eval.v1` with the
resolved context.

**Why**: One dispatcher per scope with a well-defined output type. Keeps resolvers small,
testable, and side-effect-free.

**Alternatives rejected**: Inline resolution inside each capability (duplicates logic, makes
multiplicity/fallback hard to unit-test in isolation).

## R-3. Entity matching: strategies are pure functions

**Decision**: Each strategy (`exact`, `normalise`, `alias`, `step_number`, `batch_id`) is a
pure function `(lhs, rhs, strategy_config) -> MatchOutcome`. The enum `Strategy` dispatches
to them. Normalisation is a separate pure function composed before alias lookup.

**Why**:
- Pure functions are trivially testable.
- Normalisation composes cleanly with aliases (normalise first, then fall back to aliases),
  matching the FR-002 / FR-009 contract.
- An enum reserves `custom` for a post-v1 plugin; no plugin registry is built now.

**Alternatives rejected**: OO strategy pattern with classes — over-engineered for five
strategies; makes unit tests verbose.

## R-4. Aliases file shape + reload policy

**Decision**: One YAML file per semantic domain (materials, equipment, step-names), each with
the shape:

```yaml
canonical_to_aliases:
  "Material A": ["MATERIAL-A", "Mat A", "Mat. A"]
  "Sodium Chloride": ["NaCl", "salt"]
```

Files are loaded at rule-load time (pipeline startup) and reloaded only on restart. Rules
reference a file via `entity_match.aliases_file: backend/config/rules/pilot/aliases/materials.yaml`.

**Why**:
- Per-domain files keep reviews scoped.
- Startup-only reload avoids mid-run indeterminism (Constitution VIII — Consistent).
- Explicit path per rule is more auditable than implicit directory-convention loading.

**Alternatives rejected**: Central aliases DB (migration cost with no gain); runtime hot-reload
(indeterminism risk, rejected explicitly in FR-001 edge case).

## R-5. Multiplicity, fallback, tolerance — all declared per rule

**Decision**: Each rule declares `multiplicity: first | all | error` (default `error`),
`fallback: flag_as_unevaluated | flag_as_indeterminate | treat_as_pass` (default
`flag_as_unevaluated`), and `tolerance: { kind, value, unit? }` (required for numeric
comparisons). The capability reads these directly from the resolved rule and applies them
deterministically.

**Why**: Rule authors own the risk tradeoff; the engine does not decide silently. Also makes
FR-006 (implicit equality forbidden) enforceable at the loader — missing tolerance on a
numeric comparison rule is a load-time error.

**Alternatives rejected**: Engine-wide defaults with implicit behaviour — too easy to ship
wrong-by-default rules.

## R-6. Zero-base guard for percent tolerance

**Decision**: `cross_doc_rule_eval.v1` and `page_aggregate_eval.v1` MUST guard percent
tolerance when the expected base is zero by emitting an `INDETERMINATE_ZERO_BASE` finding
(not a divide-by-zero exception, not a silent pass). Captured as an acceptance scenario
edge case.

**Why**: Real-world BMR data has zero-valued rows (planned zero usage). Silent pass would
hide a legit integrity issue; crash would halt the pipeline.

## R-7. Reverse-dependency graph extension

**Decision**: The existing `reverse_graph.py` indexes rules by `(document_role, field)`. Add
indexing of each rule's `context_object` so that:

- A correction to a value on `(role=RawMaterialPage, field=weight_kg)` invalidates every
  rule whose `context_object` reads that field.
- A correction that changes a matched entity name invalidates every rule whose
  `entity_match` keys include that entity.

The re-run planner (Spec 001) consumes the reverse graph unchanged; this spec just populates
it more comprehensively.

**Why**: FR-011 + SC-004 — selective re-run after correction hinges on accurate reverse
indexing.

**Alternatives rejected**: Run-everything-on-correction — fails SC-003 (≤ 30 s p95).

## R-8. Evidence attribution is enforced in the capability, not the engine

**Decision**: `cross_doc_rule_eval.v1` MUST emit `FindingDraft.evidence` containing a region
on each participating document (source + target). Unit tests assert that no finding emitted
by these capabilities has `len(evidence) < num_participating_docs`. Runtime assertion in
debug builds; logged in production.

**Why**: FR-005 — "missing evidence attribution is a bug". Capability-local enforcement is
defence-in-depth against regressions.

## R-9. Backward-compat: same-page evaluator path untouched

**Decision**: Rules without a `context_object` or with `context_object.scope = same_page`
route to the existing evaluator with no code change. The dispatcher checks `scope` once and
short-circuits.

**Why**: Constitution VII. Legacy rule bank passes the existing regression test unchanged.

## R-10. Test strategy

- **Unit tests per pure function**: one test file per strategy, normaliser, aliases loader,
  multiplicity policy, fallback policy.
- **Capability integration tests**: each capability against fixture rule banks covering
  happy-path, tolerance-exceeded, no-match, ambiguous-match, zero-base percent.
- **Regression**: `test_legacy_same_page_rules_unchanged.py` runs the full existing rule
  bank against a gold fixture; output diff MUST be empty.
- **Performance**: pytest mark budgets `cross_doc_rule_eval` at 50 ms p95 on pilot size.
