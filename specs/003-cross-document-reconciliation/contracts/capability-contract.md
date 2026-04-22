# Capability Contracts (Spec 003 additions)

**Feature**: 003 | **Version**: v1

Both capabilities conform to the base `Capability` ABC defined in Spec 001's
`contracts/capability-contract.md` (v2). They are atomic per Constitution III.

## `cross_doc_rule_eval.v1`

**Purpose**: Evaluate a single rule whose `context_object.scope = cross_document`. Resolves
the target document via the package manifest, applies entity matching, and compares source
and target values under the rule's declared tolerance.

**Inputs**:
- `rule: Rule` (validated, loaded)
- `source_document_ref_id: str`
- `source_page_number: int | None` (null for doc-level source fields)
- `source_field_value: any` (already extracted)
- `package_manifest: Manifest`
- `document_values_by_role: map[role, list[DocumentExtract]]`
  — `DocumentExtract` carries a document id plus its extracted fields + summaries.
- `alias_table: AliasTable | None` (resolved from `rule.context_object.entity_match.aliases_file`)

**Outputs**:
- `resolved_context: ResolvedContext` (always emitted, even on pass)
- `findings: list[FindingDraft]` (empty if within tolerance)

**Invariants**:
1. If `findings` is non-empty, every finding's `evidence` MUST reference BOTH the source
   document and the matched target document (FR-005). Unit test enforces
   `len({ref.document_ref_id for ref in finding.evidence}) >= 2`.
2. If a numeric comparison is performed, `finding.tolerance_applied` MUST be populated
   (FR-006).
3. If `context_object.tolerance.kind = percent` and expected is 0, capability MUST return
   `match_outcome=indeterminate_zero_base` and emit one finding with code
   `INDETERMINATE_ZERO_BASE` (R-6).
4. If no target document matches, the declared `fallback` MUST be applied; if `fallback =
   flag_as_unevaluated`, one finding with code `UNEVALUATED_CONTEXT_MISSING` is emitted.
5. If multiplicity is `error` and multiple matches found, emit exactly one finding with
   code `AMBIGUOUS_ENTITY_MATCH` at `scope.kind=bpcr_step` (or doc scope when no step
   context is available).

## `page_aggregate_eval.v1`

**Purpose**: Evaluate a rule whose `context_object.scope = page_aggregate`. Applies the
`page_selector`, computes the `aggregation`, compares to `expected` under tolerance.

**Inputs**:
- `rule: Rule`
- `document_ref_id: str`
- `pages: list[PageExtract]` (each page's extracted fields + summaries)
- `expected_value: number | null`
- `expected_source: { field, document_ref_id?, page_number? }`

**Outputs**:
- `resolved_context: ResolvedContext`
- `findings: list[FindingDraft]`

**Invariants**:
1. `resolved_context.participating_pages` MUST list every page that contributed a value.
2. If any selected page has a null/missing value for the aggregated field, behaviour
   follows `fallback` (default `flag_as_indeterminate` per FR-007 with evidence citing the
   missing pages).
3. Aggregation functions supported in v1: `sum`, `count`, `min`, `max`, `avg`. Others MUST
   cause a load-time error.
4. Percent tolerance with zero expected ⇒ `INDETERMINATE_ZERO_BASE` as in cross-doc.

## Shared contract requirements

- Neither capability writes to DB; the stage orchestrator persists `ResolvedContext` and
  `Finding` via their respective stores.
- Both emit structured logs with `rule_id`, `rule_version`, `match_outcome`,
  `participating_document_ids`, and `elapsed_ms`.
- Timeouts: per-evaluation budget 250 ms (enforced via `CapabilityContext.deadline`);
  exceeding the budget emits `rule_evaluation_timeout` as a finding tagged
  `severity=minor, code=TIMEOUT` with fallback semantics.
