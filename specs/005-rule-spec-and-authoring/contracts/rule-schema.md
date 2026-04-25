# Rule Schema Contract (v1.0)

**Feature**: 005 | **Schema version**: `1.0` | **File**: `backend/config/rules/schema/rule.schema.v1.0.json`

Human-readable reference for the JSON Schema that authoritatively defines rule YAML.
Draft: JSON Schema 2020-12.

## Root object

```jsonc
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://docs-digitization.local/rule.schema.v1.0.json",
  "title": "BMR Audit Rule",
  "type": "object",
  "required": [
    "schema_version","id","version","severity","alcoa_tag",
    "description","context_object","source"
  ],
  "properties": {
    "schema_version": { "const": "1.0" },
    "id": { "type": "string", "pattern": "^[a-z][a-z0-9._-]*$" },
    "version": { "type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+$" },
    "severity": { "enum": ["critical","major","minor","info"] },
    "alcoa_tag": { "enum": [
      "Attributable","Legible","Contemporaneous","Original","Accurate",
      "Consistent","Enduring","Complete","Available"
    ]},
    "gmp_category": { "type": "string" },
    "description": { "type": "string", "minLength": 10 },
    "context_object": { "$ref": "#/$defs/ContextObject" },
    "source": { "$ref": "#/$defs/FieldRef" },
    "target": { "$ref": "#/$defs/FieldRef" },
    "expected": { "$ref": "#/$defs/FieldRef" },
    "tolerance": { "$ref": "#/$defs/Tolerance" },
    "multiplicity": { "enum": ["first","all","error"] },
    "fallback": { "enum": ["flag_as_unevaluated","flag_as_indeterminate","treat_as_pass"] },
    "synthesises_from": { "type":"array", "items":{ "type":"string" } }
  },
  "allOf": [
    { "$ref": "#/$defs/CrossDocumentRequirements" },
    { "$ref": "#/$defs/PageAggregateRequirements" },
    { "$ref": "#/$defs/SamePageForbidsCrossScopeKeys" }
  ],
  "unevaluatedProperties": false
}
```

## `$defs.ContextObject`

```jsonc
{
  "type": "object",
  "required": ["scope"],
  "properties": {
    "scope": { "enum": ["same_page","cross_document","page_aggregate"] },

    "role": { "type":"string" },
    "entity_match": {
      "type":"object",
      "required": ["strategy"],
      "properties": {
        "strategy": { "enum": ["exact","normalise","alias","step_number","batch_id","custom"] },
        "normalise": { "type":"boolean" },
        "case_insensitive": { "type":"boolean" },
        "punctuation_strip": { "type":"array", "items":{ "type":"string", "maxLength": 3 } },
        "aliases_file": { "type":"string" }
      },
      "unevaluatedProperties": false
    },

    "page_selector": {
      "type":"object",
      "required": ["document_role","page_filter"],
      "properties": {
        "document_role": { "type":"string" },
        "page_filter": { "enum": ["all_bpcr_step_pages","first_page","last_page","by_index","by_tag"] },
        "page_indices": { "type":"array", "items":{ "type":"integer", "minimum": 1 } },
        "page_tag": { "type":"string" }
      },
      "unevaluatedProperties": false
    },

    "aggregation": { "enum": ["sum","count","min","max","avg"] }
  },
  "unevaluatedProperties": false
}
```

## `$defs.FieldRef`

```jsonc
{
  "type":"object",
  "required":["field"],
  "properties": {
    "field": { "type":"string" },
    "scope_hint": { "type":"string" },
    "document_ref_hint": { "type":"string" }
  },
  "unevaluatedProperties": false
}
```

## `$defs.Tolerance`

```jsonc
{
  "type":"object",
  "required":["kind","value"],
  "properties": {
    "kind": { "enum":["absolute","percent","relative"] },
    "value": { "type":"number", "exclusiveMinimum": 0 },
    "unit": { "type":"string" }
  },
  "unevaluatedProperties": false
}
```

## Conditional requirement subschemas

### `CrossDocumentRequirements`

```jsonc
{
  "if": { "properties": { "context_object": { "properties": { "scope": { "const": "cross_document" } } } } },
  "then": {
    "required": ["target"],
    "properties": {
      "context_object": {
        "required": ["role","entity_match"]
      }
    }
  }
}
```

### `PageAggregateRequirements`

```jsonc
{
  "if": { "properties": { "context_object": { "properties": { "scope": { "const": "page_aggregate" } } } } },
  "then": {
    "properties": {
      "context_object": { "required": ["page_selector","aggregation"] }
    }
  }
}
```

### `SamePageForbidsCrossScopeKeys`

```jsonc
{
  "if": { "properties": { "context_object": { "properties": { "scope": { "const": "same_page" } } } } },
  "then": {
    "properties": {
      "context_object": {
        "not": {
          "anyOf": [
            { "required": ["role"] },
            { "required": ["entity_match"] },
            { "required": ["page_selector"] },
            { "required": ["aggregation"] }
          ]
        }
      }
    }
  }
}
```

## Numeric-tolerance rule (author-facing)

The schema MAY permit rules without `tolerance`; the loader enforces the numeric-field
requirement (FR from Spec 003). The validator surfaces this as a blocking error with
`fix_hint` suggesting a default `tolerance` block.

## Minimal valid rules

### Cross-document

```yaml
schema_version: "1.0"
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
    aliases_file: backend/config/rules/pilot/aliases/materials.yaml
source: { field: dispensed_weight_kg, scope_hint: bpcr_step_page }
target: { field: weight_kg }
tolerance: { kind: absolute, value: 0.1, unit: kg }
```

### Page aggregate

```yaml
schema_version: "1.0"
id: alcoa.accurate.bpcr-step-sum-vs-batch-target
version: 1.0.0
severity: major
alcoa_tag: Accurate
description: "Sum of BPCR step weights must match batch target within 0.5%"
context_object:
  scope: page_aggregate
  page_selector:
    document_role: BPCR
    page_filter: all_bpcr_step_pages
  aggregation: sum
source: { field: dispensed_weight_kg }
expected: { field: batch_target_weight_kg, document_ref_hint: BMR }
tolerance: { kind: percent, value: 0.5 }
fallback: flag_as_indeterminate
```

### Same-page (legacy)

```yaml
schema_version: "1.0"
id: alcoa.attributable.bpcr-step-initials-present
version: 1.0.0
severity: major
alcoa_tag: Attributable
description: "Each BPCR step page must have operator initials"
context_object: { scope: same_page }
source: { field: operator_initials, scope_hint: bpcr_step_page }
```
