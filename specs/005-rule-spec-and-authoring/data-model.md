# Data Model: Rule-Spec Schema & Authoring

**Feature**: 005 | **Spec Version**: v1

This spec's "data model" is primarily a **YAML schema** + a handful of lightweight
author-facing entities produced by the validator and skill. No new DB tables.

## 1. The Rule YAML (schema v1.0 shape)

All fields below are formalised in
`backend/config/rules/schema/rule.schema.v1.0.json`. This table is a human-readable
summary; the JSON Schema is the source of truth.

### 1.1 Top-level

| Field | Required | Type | Notes |
|---|---|---|---|
| `schema_version` | yes | string | exact match to a published schema (`"1.0"` for v1) |
| `id` | yes | string | unique within a rule bank; dot-separated, e.g. `alcoa.accurate.bpcr-raw-material-weight-match` |
| `version` | yes | string | semver of the rule itself |
| `severity` | yes | enum | `critical` / `major` / `minor` / `info` |
| `alcoa_tag` | yes | enum | Attributable / Legible / Contemporaneous / Original / Accurate / Consistent / Enduring / Complete / Available |
| `gmp_category` | no | string | set for GMP rules; YAML-extensible |
| `description` | yes | string | 1–3 sentences |
| `context_object` | yes | object | see §1.2 |
| `source` | yes | object | `{ field: string, scope_hint?: string }` |
| `target` | conditional | object | required iff `context_object.scope = cross_document`; `{ field: string }` |
| `expected` | conditional | object | required iff `context_object.scope = page_aggregate` and the rule compares an aggregate to an externally-declared expected value |
| `tolerance` | conditional | object | REQUIRED when `source.field` is numeric; `{ kind: absolute | percent | relative, value: number, unit?: string }` |
| `multiplicity` | no | enum | `first` / `all` / `error` (default `error`) |
| `fallback` | no | enum | `flag_as_unevaluated` / `flag_as_indeterminate` / `treat_as_pass` (default `flag_as_unevaluated`) |
| `synthesises_from` | no | array[string] | for checklist synthesis; ids of contributing ALCOA/GMP rules |

### 1.2 `context_object` shape

```yaml
context_object:
  scope: same_page | cross_document | page_aggregate
  # cross_document fields
  role?: string                                # document role (from manifest)
  entity_match?:
    strategy: exact | normalise | alias | step_number | batch_id | custom
    normalise?: bool
    case_insensitive?: bool
    punctuation_strip?: list[string]
    aliases_file?: path
  # page_aggregate fields
  page_selector?:
    document_role: string
    page_filter: all_bpcr_step_pages | first_page | last_page | by_index | by_tag
    page_indices?: list[int]
    page_tag?: string
  aggregation?: sum | count | min | max | avg
```

Conditional validation rules (enforced by JSON Schema `if/then/else`):

1. `scope=cross_document` ⇒ `role` + `entity_match` required; `target` required.
2. `scope=page_aggregate` ⇒ `page_selector` + `aggregation` required.
3. `scope=same_page` ⇒ `role`, `entity_match`, `page_selector`, `aggregation` MUST NOT
   appear (forbids accidental cross-scope leakage).
4. `entity_match.aliases_file` MUST be a string that the loader can resolve; the schema
   checks type/format only, runtime loader enforces existence.

## 2. Author-facing ephemeral entities

### 2.1 `ValidationReport`
Produced by `bmr-rules validate` and by the skill after YAML draft.

| Field | Type | Notes |
|---|---|---|
| `rule_id` | string | |
| `schema_version` | string | |
| `errors` | `list[ValidationError]` | empty on pass |
| `warnings` | `list[ValidationWarning]` | non-blocking |

### 2.2 `ValidationError`
| Field | Type | Notes |
|---|---|---|
| `path` | string | JSON pointer into the rule YAML |
| `message` | string | author-facing, not raw jsonschema |
| `fix_hint` | string, nullable | suggested YAML snippet |
| `severity` | enum | `blocking` / `warning` |

### 2.3 `FixtureRunReport`
Produced by `bmr-rules fixture-run` and by the skill.

| Field | Type | Notes |
|---|---|---|
| `rule_id` | string | |
| `positive_fixtures` | list | `[{fixture_path, expected_finding_count, actual_finding_count, sample_finding?: {id, evidence}}]` |
| `negative_fixtures` | list | `[{fixture_path, expected_finding_count: 0, actual_finding_count, false_positive?: bool}]` |
| `verdict` | enum | `ready` / `not_ready` |
| `notes` | list[string] | |

### 2.4 `TuneProposal` (skill, tune mode)
| Field | Type | Notes |
|---|---|---|
| `rule_id` | string | |
| `current_value` | any | the field's current value (e.g. tolerance) |
| `proposed_value` | any | |
| `rationale` | string | plain English |
| `supporting_samples` | list[string] | feedback_sample ids cited |
| `diff` | string | unified YAML diff |

### 2.5 `MigrationReport` (skill, migrate mode)
| Field | Type | Notes |
|---|---|---|
| `source_path` | string | Python file migrated from |
| `rule_id_suggested` | string | |
| `yaml_draft_path` | string | |
| `todos` | list | `[{line_in_source, reason, schema_gap: bool}]` |
| `verdict` | enum | `ready` / `needs_human` |

## 3. Relationship to other specs' entities

- `ValidationReport.errors` come from schema validation against
  `backend/config/rules/schema/rule.schema.vX.Y.json`. The same schema is loaded by Spec
  003's `RuleLoader` (CI parity test enforces byte equality).
- `FixtureRunReport` uses Spec 001's fixture runner entry point under the hood.
- `TuneProposal.supporting_samples` cite Spec 004's `FeedbackSample.id` values.
- `MigrationReport.todos` MAY cite Spec 001's `Capability` registry for gaps that would
  require new capabilities (expected to be rare; most gaps are expressibility in the
  schema).

## 4. Versioning & governance

- Every rule YAML MUST declare `schema_version`. Loader refuses to start if unknown.
- New schema versions are additive-first; breaking changes must be approved with a
  CHANGELOG entry and a migration note.
- The schema CHANGELOG lives at `backend/config/rules/schema/CHANGELOG.md` and uses a
  "Keep a Changelog" format.
