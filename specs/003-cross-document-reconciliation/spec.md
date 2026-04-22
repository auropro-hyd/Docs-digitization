# Feature Specification: Cross-Document Rule Support

**Feature Branch**: `003-cross-document-reconciliation`
**Created**: 2026-04-17
**Last Revised**: 2026-04-17 (v2 — reframed from "dedicated reconciliation engine" to "cross-document capability of the existing rule engine", per Constitution v1.1.0)
**Status**: Draft
**Input**: User description: "Extend the existing rule engine so a single rule can evaluate inputs drawn from multiple documents in a BMR package, using a `context_object` declaration to specify the other role, the entity-matching strategy, and the comparison tolerance. No separate reconciliation engine — the rule engine does the work."

## Scope clarification (reframed from v1)

v1 of this spec proposed a dedicated cross-document reconciliation engine with its own entity
resolver, tolerance engine, and evidence synthesiser. The directional review (Akhilesh call,
2026-04-17) concluded this was duplicative: the rule engine already owns rule loading,
evidence attachment, tolerance handling, and finding emission. The v2 scope is **only** to
extend the rule engine with:

1. A declarative `context_object` block on each rule that describes how to pull inputs from
   other documents in the package.
2. Two new capabilities (`cross_doc_rule_eval.v1` and `page_aggregate_eval.v1`) that the
   rule engine invokes when a rule's `context_object` is cross-document or page-aggregate.
3. Entity-matching strategies as **configuration**, not as a separate code subsystem.

The rule-spec schema itself lives in spec 005. This spec owns the runtime semantics and the
user-visible behaviour.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Reviewer sees a quantity mismatch between BPCR and raw-material page surfaced as a single finding (Priority: P1)

The BPCR step-3 page records `12.5 kg of Material A dispensed`. The corresponding RawMaterialPage records `12.4 kg of Material A`. A single rule with `context_object.role: RawMaterialPage` and `entity_match.strategy: material_name` (with `tolerance: absolute 0.1 kg`) produces one finding citing both documents.

**Why this priority**: This is the canonical cross-doc check the product must demonstrate; without it the audit cannot catch the most common data-integrity issues.

**Independent Test**: Fixture with planted 0.2 kg mismatch between BPCR step and raw-material page. Verify one finding is emitted, tagged ALCOA:Accurate, severity Major, with evidence citing both documents and `tolerance_applied` populated.

**Acceptance Scenarios**:

1. **Given** a rule declares `context_object: { scope: cross_document, role: RawMaterialPage, entity_match: { strategy: material_name } }` and `tolerance: { kind: absolute, value: 0.1, unit: kg }`, **When** the rule engine evaluates the BPCR step-3 page, **Then** it looks up the RawMaterialPage document by role in the package manifest, finds the row matching `material_name = "Material A"`, compares weights within tolerance, and emits one finding with evidence from both documents.
2. **Given** the comparison is within tolerance, **When** evaluation completes, **Then** no finding is emitted (the rule passes silently; only exceedances produce findings).
3. **Given** the tolerance is exceeded, **When** the finding is emitted, **Then** it contains `raw_value`, `expected_value`, and `tolerance_applied` so a reviewer can verify the comparison without re-running the pipeline.

---

### User Story 2 — Rule evaluates a within-document page aggregate (Priority: P1)

A rule needs to assert the sum of per-step dispensed quantities on the BPCR equals the batch-level target within 0.5%. The rule declares `context_object.scope: page_aggregate, aggregation: sum, page_selector: all_bpcr_step_pages` and `tolerance: { kind: percent, value: 0.5 }`.

**Why this priority**: Many compliance checks are within-document aggregations; without page-aggregate support they would require new Python for each one.

**Independent Test**: Fixture with planted summation discrepancy. Verify one finding with `scope: { kind: document, ... }` summarising the mismatch, citing the participating pages as evidence.

**Acceptance Scenarios**:

1. **Given** a rule declares `page_aggregate` with `aggregation: sum`, **When** the rule engine evaluates it, **Then** `page_aggregate_eval.v1` sums the extracted values across the selected pages, compares against the expected, and emits at most one finding at document scope.
2. **Given** any source page has no extracted value for the aggregated field, **When** evaluation runs, **Then** the rule's declared `fallback` applies (typically `flag_as_indeterminate` with evidence citing which pages are missing data).

---

### User Story 3 — Entity matching tolerates minor naming variations (Priority: P1)

BPCR spells `"Material A"` but the raw-material page header uses `"MATERIAL-A"`. Entity matching normalises (case + whitespace + configurable delimiter stripping) and still matches. For non-trivial variations (synonyms, abbreviations), the rule can point at an aliases file.

**Why this priority**: Exact-match strategies are brittle on real OCR output. A small set of normalisation + optional aliases is the difference between the engine working and constant false positives.

**Independent Test**: Planted fixture with normalisation + alias variants. Verify matches succeed for both without a finding, and no match produces a `UNEVALUATED_CONTEXT_MISSING` finding with the rule's fallback.

**Acceptance Scenarios**:

1. **Given** `entity_match: { strategy: material_name, normalise: true }`, **When** matching runs, **Then** case, whitespace, and configured punctuation are stripped before comparison.
2. **Given** `entity_match.aliases_file: "backend/config/rules/pilot/aliases/materials.yaml"`, **When** normalisation fails, **Then** the aliases file is consulted before declaring no match.
3. **Given** no match can be found after normalisation and aliases, **When** the rule evaluates, **Then** the rule's declared `fallback` applies (`flag_as_unevaluated` emits an `UNEVALUATED_CONTEXT_MISSING` finding pointing at the missing counterpart).

---

### User Story 4 — Rule references a BPCR step by number to reconcile with checklist (Priority: P2)

A checklist rule asserts "Step 3 (Weighing) has a 'check by' signature". The rule's `context_object.role: ChecklistPage, entity_match.strategy: step_number` looks up the checklist row for step 3 and evaluates the signature column.

**Why this priority**: Step-number matching is a specific but common strategy. It's P2 because `material_name` covers the bigger demo cases first.

**Independent Test**: Fixture with planted missing checklist signature for step 3. Verify the finding is emitted at `scope: { kind: bpcr_step, step_number: 3 }` and cites both documents.

**Acceptance Scenarios**:

1. **Given** a rule with `entity_match.strategy: step_number`, **When** evaluated for BPCR step 3, **Then** it looks up the checklist row whose step number is 3 and evaluates the rule's field (e.g., signature present).
2. **Given** the checklist row is missing, **When** evaluated, **Then** the rule's `fallback` applies.

---

### User Story 5 — Reviewer sees the rule's context_object in the finding (Priority: P2)

When a reviewer clicks into a cross-doc finding, the detail view shows (a) which rule fired, (b) the resolved context_object (role + match key + matched document), and (c) the values on both sides with the tolerance applied.

**Why this priority**: Transparency of cross-doc reasoning is what makes findings defensible. P2 because a compact view is usable for the demo; the full reveal is a later UX polish.

**Independent Test**: Fixture finding. Verify the detail payload includes the rule's context_object declaration and the resolved match key.

**Acceptance Scenarios**:

1. **Given** a cross-doc finding, **When** the reviewer opens detail, **Then** the UI shows `context_object: { scope: cross_document, role: RawMaterialPage, entity_match: { strategy: material_name, matched_key: "Material A" } }` verbatim.
2. **Given** the `tolerance_applied` is populated, **When** viewed, **Then** it's rendered alongside raw and expected values.

---

### Edge Cases

- Multiple rows in the target role match the same key: rule declares a `multiplicity` policy (`first | all | error`); default `error` emits `AMBIGUOUS_ENTITY_MATCH` finding.
- Target role is entirely missing from the package: rule's `fallback` applies.
- `aliases_file` path is invalid at rule load: pipeline refuses to start (hard error).
- Target document exists but is `FAIL`-legibility: rule emits a finding tagged `contributing_factor: target_low_legibility` and applies fallback semantics.
- Numeric tolerance is `percent` but expected value is zero: guard returns `INDETERMINATE_ZERO_BASE` rather than divide-by-zero.
- Aggregation requested but selected pages produce zero values: finding cites "no pages matched selector" with rule's fallback.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The rule engine MUST load and validate each rule's `context_object` block at startup. Rules with unresolvable `context_object` (missing role, unknown strategy, missing aliases file) MUST cause the pipeline to fail fast with a clear error pointing at the rule.
- **FR-002**: At evaluation time, the engine MUST resolve `context_object.scope = cross_document` by: (a) looking up documents by `role` in the package's manifest; (b) applying `entity_match` normalisation; (c) consulting `aliases_file` if normalisation fails; (d) applying `multiplicity` policy.
- **FR-003**: At evaluation time, the engine MUST resolve `context_object.scope = page_aggregate` by applying the declared `page_selector` and `aggregation` to the source document's extracted fields + summaries.
- **FR-004**: Cross-document rule evaluation MUST run via the `cross_doc_rule_eval.v1` capability. Page-aggregate evaluation MUST run via the `page_aggregate_eval.v1` capability. Rules that have `context_object.scope = same_page` continue to run through the existing same-page evaluator path unchanged.
- **FR-005**: Findings produced via cross-doc or page-aggregate evaluation MUST attach evidence from EVERY document whose value participated in the evaluation. Missing evidence attribution is a bug.
- **FR-006**: Numeric comparisons MUST apply the rule's declared `tolerance` and record `tolerance_applied` on the finding. Implicit equality (no declared tolerance) on numeric fields is a violation (Constitution VIII — Accurate).
- **FR-007**: When entity matching cannot resolve a required counterpart, the rule's declared `fallback` (`flag_as_unevaluated`, `flag_as_indeterminate`, `treat_as_pass`) MUST be applied. The default when unspecified is `flag_as_unevaluated`.
- **FR-008**: When `multiplicity` is triggered (multiple matches), the rule's policy (`first | all | error`, default `error`) determines behaviour: `error` emits one `AMBIGUOUS_ENTITY_MATCH` finding at the BPCR step scope; `all` evaluates the rule against each match; `first` picks deterministically by a declared tie-breaker.
- **FR-009**: The entity-match configuration surface MUST support: `strategy ∈ { exact, normalise, alias, step_number, batch_id, custom }`, with `normalise: bool`, `case_insensitive: bool`, `punctuation_strip: list[str]`, `aliases_file: path`.
- **FR-010**: The finding detail surface MUST expose the resolved `context_object` (role, match key, matched document id) so the reviewer can inspect how the rule reached its verdict.
- **FR-011**: The rule engine's reverse-dependency graph (used by the re-run planner, spec 001) MUST index each rule's `context_object` so that a correction in a target document correctly re-runs the rules whose `context_object` reads that target.
- **FR-012**: The existing same-page rule evaluation path MUST remain unchanged and MUST NOT regress. Rules without `context_object` (legacy) continue to behave as before.

### Key Entities *(include if feature involves data)*

- **Rule** (extended from existing): adds a `context_object` block, `fallback`, `multiplicity`, `synthesises_from` (checklist only). Schema owned by Spec 005.
- **ResolvedContext**: Ephemeral runtime artefact per rule invocation. Carries `scope`, `matched_role` (if cross-doc), `matched_key`, `matched_document_id`, `participating_pages` (if aggregate), and evidence pointers. Logged into the audit trail on finding emission.
- **AliasTable**: Declarative YAML mapping `canonical -> [alias, ...]` per semantic domain (materials, equipment, step-names). Loaded at rule-load time; reloadable only at pipeline startup.
- **Tolerance**: Value type shared with spec 001. Not redeclared here.
- **EntityMatch**: The strategy block inside `context_object`. Fields listed in FR-009. Deterministic.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On the pilot fixture, cross-document quantity-mismatch findings have precision ≥ 95% against gold-standard auditor markup (very few false positives).
- **SC-002**: On the pilot fixture, cross-document signature / check-by findings have recall ≥ 95% (very few misses).
- **SC-003**: Adding a new cross-document check requires ONLY a new YAML rule entry with a `context_object` — zero Python code changes. Verified by a test that adds a synthetic rule and observes it firing correctly on a fixture.
- **SC-004**: A one-value correction on a target document triggers re-evaluation of only the rules whose `context_object` reads that target (measured via the re-run planner's reverse-graph output). Complete in under 30s p95 (cross-check with spec 001 SC-003).
- **SC-005**: Alias file additions take effect on the next pipeline run without Python changes; verified by a test that adds an alias and observes a prior non-match becoming a match.
- **SC-006**: Existing same-page rule evaluation retains its pre-change passing-test count (Constitution VII regression gate).

## Assumptions

1. **Manifest has reliable role labels** (Spec 002). Cross-document lookup cannot work if classification is wrong; reviewer overrides fix that upstream.
2. **Aliases are curated, not inferred**. The engine does not learn aliases at runtime; it reads the YAML file authored by SME + operator. Spec 005's authoring skill can suggest aliases from the feedback corpus.
3. **Entity-match strategies in v1**: `exact`, `normalise`, `alias`, `step_number`, `batch_id`. `material_name` is a specialisation of `normalise + alias`. A generic `custom` plug-in is deferred.
4. **No separate reconciliation engine subsystem**. All logic lives in the existing rule engine + two new capabilities (`cross_doc_rule_eval.v1`, `page_aggregate_eval.v1`). This is a deliberate simplification vs v1 of this spec.
5. **Tolerance semantics are declared per rule, not per capability**. Capabilities read the tolerance from the rule.
6. **Existing same-page evaluation path is the baseline** and this spec's changes are strictly additive (new `context_object` surface, legacy rules untouched).
