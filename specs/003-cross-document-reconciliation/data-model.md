# Data Model: Cross-Document Rule Support

**Feature**: 003 | **Spec Version**: v2

This spec introduces one persisted entity (`ResolvedContext`), one config-loaded entity
(`AliasTable`), and schema extensions to the existing `Rule` (schema owned by Spec 005).

## 1. Enumerations

### 1.1 `ContextScope`
`same_page` | `cross_document` | `page_aggregate`

### 1.2 `EntityMatchStrategy`
`exact` | `normalise` | `alias` | `step_number` | `batch_id` | `custom`

### 1.3 `MultiplicityPolicy`
`first` | `all` | `error`

### 1.4 `FallbackPolicy`
`flag_as_unevaluated` | `flag_as_indeterminate` | `treat_as_pass`

### 1.5 `MatchOutcomeKind`
`matched` | `not_matched` | `ambiguous` | `indeterminate_zero_base`

## 2. Core Entities

### 2.1 `ResolvedContext` (runtime + audit-log persisted)

Captured every time a rule with non-`same_page` scope is evaluated. One row per evaluation.

| Field | Type | Notes |
|---|---|---|
| `id` | ULID | PK |
| `run_id` | FK → audit-run | |
| `rule_id` | string | rule id from YAML (schema §Spec 005) |
| `rule_version` | string | semver of rule spec |
| `scope` | `ContextScope` | |
| `matched_role` | string, nullable | set iff scope=cross_document |
| `matched_key` | string, nullable | normalised key used for the match |
| `matched_document_id` | FK → `DocumentRef`, nullable | |
| `participating_pages` | JSONB: `[{document_ref_id, page_number}]`, nullable | set iff scope=page_aggregate |
| `entity_match_strategy` | `EntityMatchStrategy`, nullable | |
| `match_outcome` | `MatchOutcomeKind` | |
| `tolerance_applied` | JSONB: `{kind, value, unit?}`, nullable | copied from rule for traceability |
| `fallback_applied` | `FallbackPolicy`, nullable | set iff the fallback fired |
| `finding_id` | FK → `Finding`, nullable | populated after finding emission |
| `created_at` | timestamp | |

### 2.2 `AliasTable` (config-loaded, cached)

| Field | Type | Notes |
|---|---|---|
| `id` | ULID | PK |
| `domain` | string | e.g. `materials`, `equipment`, `step_names` |
| `yaml_path` | string | absolute path loaded at startup |
| `version` | string | semver; bumped on edit |
| `loaded_at` | timestamp | |
| `canonical_to_aliases` | JSONB: `{canonical: [alias, ...]}` | |

Aliases are read into memory at startup and cached per `yaml_path`. Rule loads reference
tables by `yaml_path`, not by id — path IS the identity.

### 2.3 Rule extensions (schema owned by Spec 005; quoted here for runtime clarity)

A rule YAML MAY carry these additional fields; default is `context_object.scope=same_page`
(or omitted, which is equivalent).

```yaml
id: alcoa.accurate.bpcr-raw-material-weight-match
version: 1.0.0
severity: major
alcoa_tag: Accurate
description: "BPCR dispensed weight must match raw-material page within 0.1 kg"

context_object:
  scope: cross_document
  role: RawMaterialPage
  entity_match:
    strategy: normalise
    normalise: true
    case_insensitive: true
    punctuation_strip: ["-", "_", "."]
    aliases_file: backend/config/rules/pilot/aliases/materials.yaml
  multiplicity: error           # first | all | error
  fallback: flag_as_unevaluated # flag_as_unevaluated | flag_as_indeterminate | treat_as_pass

source:
  field: dispensed_weight_kg
  scope_hint: bpcr_step_page
target:
  field: weight_kg

tolerance:
  kind: absolute
  value: 0.1
  unit: kg
```

Page-aggregate variant:

```yaml
id: alcoa.accurate.bpcr-step-sum-vs-batch-target
context_object:
  scope: page_aggregate
  page_selector:
    document_role: BPCR
    page_filter: all_bpcr_step_pages
  aggregation: sum
  fallback: flag_as_indeterminate
source:
  field: dispensed_weight_kg
expected:
  field: batch_target_weight_kg
tolerance:
  kind: percent
  value: 0.5
```

## 3. Cross-Entity Validation Rules

1. A rule with a numeric `source.field` MUST declare `tolerance` (FR-006). Enforced at rule
   load; missing tolerance is a hard error.
2. A rule with `context_object.scope = cross_document` MUST declare `role` and
   `entity_match.strategy` (FR-002).
3. A rule with `entity_match.aliases_file` set MUST point to a loadable `AliasTable`; an
   unresolved path is a hard load error (FR-001 edge case).
4. A rule with `context_object.scope = page_aggregate` MUST declare `page_selector` and
   `aggregation` (FR-003).
5. Every emitted cross-doc `Finding` MUST have `len(evidence) >= 2` (source + at least one
   target) (FR-005).
6. `ResolvedContext.finding_id` MAY be null (tolerance passed silently → no finding); when
   populated, it MUST exactly match the emitted `Finding.id`.
7. The reverse-dependency graph (Spec 001 data-model) MUST contain an edge from each rule
   to every `(role, field)` pair referenced in its `context_object` + `target.field`.

## 4. Persistence Strategy

- Table: `resolved_context` — append-only, partitioned by `run_id`.
- Aliases are in-memory only; not persisted to DB (source of truth is the YAML file in git).
- The rule engine persists a `rules_manifest` snapshot per run (already exists in Spec 001
  data-model) capturing rule id + version + `context_object` digest — this spec extends
  that snapshot record to include `context_object_digest` for reproducibility.

## 5. Relationship to Spec 001 entities

- `ResolvedContext.finding_id` → Spec 001 `Finding.id`.
- `ResolvedContext.run_id` → Spec 001 `Run.id`.
- `FindingDraft.source` (from capability contract) is set to the capability id:
  `cross_doc_rule_eval.v1` or `page_aggregate_eval.v1`.
- Selective re-run planner (Spec 001) reads the reverse-dependency graph populated by this
  spec's loader extension.
