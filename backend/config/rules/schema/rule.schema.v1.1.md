# BMR Audit Rule (v1.1)

> Generated from `rule.schema.v1.1.json`. Do not edit by hand;
> run `python -m app.bmr.rules.docgen` to regenerate.

Schema v1.1 for BMR audit rule YAMLs. Additive over v1.0: page_selector gains an optional section_id (Spec 007).

## Top-level properties

### `alcoa_tag` (**required**)

- Type: one of (see enum list below)
- Allowed values:

- `Attributable`
- `Legible`
- `Contemporaneous`
- `Original`
- `Accurate`
- `Consistent`
- `Enduring`
- `Complete`
- `Available`

### `context_object` (**required**)

- Type: see [ContextObject](#contextobject)

### `description` (**required**)

- Type: `string`
- Constraint: minLength = `10`

### `id` (**required**)

- Type: `string`
- Constraint: minLength = `3`
- Constraint: maxLength = `200`
- Constraint: pattern = `^[a-z][a-z0-9._-]*$`

### `schema_version` (**required**)

- Type: must equal `'1.1'`

### `severity` (**required**)

- Type: one of: `critical`, `major`, `minor`, `info`

### `version` (**required**)

- Type: `string`
- Constraint: pattern = `^[0-9]+\.[0-9]+\.[0-9]+$`

### `deprecated` (optional)

When true, the loader accepts the rule (so prior runs remain reproducible) but the compliance stage skips it. Spec 005 FR-013.

- Type: `boolean`

### `expected` (optional)

- Type: see [FieldRef](#fieldref)

### `fallback` (optional)

- Type: one of: `flag_as_unevaluated`, `flag_as_indeterminate`, `treat_as_pass`

### `gmp_category` (optional)

- Type: `string`
- Constraint: minLength = `1`

### `multiplicity` (optional)

- Type: one of: `first`, `all`, `error`

### `source` (optional)

- Type: see [FieldRef](#fieldref)

### `superseded_by` (optional)

Human-readable pointer to the rule that replaces this one (e.g. 'alcoa.accurate.bpcr-weight-match@2.0.0'). Informational only.

- Type: `string`
- Constraint: minLength = `3`

### `synthesises_from` (optional)

- Type: `array` of `string`

### `target` (optional)

- Type: see [FieldRef](#fieldref)

### `tolerance` (optional)

- Type: see [Tolerance](#tolerance)

## Conditional requirements

- **SourceRequiredForLeafScopes** — if `context_object.scope` is one of `same_page`, `cross_document`, `page_aggregate`, the rule must declare `source`.
- **CrossDocumentRequirements** — if `context_object.scope == "cross_document"`, the rule must declare `target`; `context_object` must declare `role`, `entity_match`.
- **PageAggregateRequirements** — if `context_object.scope == "page_aggregate"`, `context_object` must declare `page_selector`, `aggregation`.
- **SamePageForbidsCrossScopeKeys** — if `context_object.scope == "same_page"`, `context_object` must NOT declare `role`, `entity_match`, `page_selector`, `aggregation`.
- **ChecklistSynthesisRequirements** — if `context_object.scope == "checklist_synthesis"`, the rule must declare `synthesises_from`; `context_object` must NOT declare `role`, `entity_match`, `page_selector`, `aggregation`.

## Referenced definitions

### `ChecklistSynthesisRequirements`

### `ContextObject`

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `aggregation` | no | one of: `sum`, `count`, `min`, `max`, `avg` | enum: `sum`, `count`, `min`, `max`, `avg` |
| `entity_match` | no | `object` |  |
| `group_by` | no | one of: `bpcr_step`, `document_scope`, `rule`, `none` | enum: `bpcr_step`, `document_scope`, `rule`, `none` |
| `page_selector` | no | `object` |  |
| `role` | no | `string` | minLength = `1` |
| `scope` | yes | one of: `same_page`, `cross_document`, `page_aggregate`, `checklist_synthesis` | enum: `same_page`, `cross_document`, `page_aggregate`, `checklist_synthesis` |

### `CrossDocumentRequirements`

### `FieldRef`

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `document_ref_hint` | no | `string` | minLength = `1` |
| `field` | yes | `string` | minLength = `1` |
| `scope_hint` | no | `string` | minLength = `1` |

### `PageAggregateRequirements`

### `SamePageForbidsCrossScopeKeys`

### `SourceRequiredForLeafScopes`

### `Tolerance`

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `kind` | yes | one of: `absolute`, `percent`, `relative` | enum: `absolute`, `percent`, `relative` |
| `unit` | no | `string` | minLength = `1` |
| `value` | yes | `number` | > `0` |
