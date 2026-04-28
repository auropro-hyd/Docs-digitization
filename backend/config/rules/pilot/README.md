# Pilot Rule Bank — Working Examples Per Rule Type

Three rule scopes are wired in the BMR engine, plus a synthesis scope on top.
Every entry below is a **working** YAML in this bank — it parses, it loads,
it fires against the pilot fixture.

| Scope | YAML in this bank | What it verifies |
|---|---|---|
| `same_page` | `bank/alcoa_attributable_operator_signature.yaml` | Individual verification — does this single page satisfy a per-page check? |
| `cross_document` | `bank/alcoa_accurate_bpcr_weight_match.yaml` | Cross-document verification — does a value on doc A match the corresponding value on doc B? |
| `page_aggregate` | `bank/alcoa_accurate_bpcr_step_sum.yaml` | Aggregated verification — sum / mean / count across many pages compared to a single expected value. |
| `checklist_synthesis` | `bank/checklist_bpcr_step_complete.yaml` | Roll-up — turn N constituent findings into one per-step verdict. |

---

## How to run them

From the `backend/` directory, with your venv active:

```bash
# 1. Validate every rule against its declared schema_version.
uv run bmr-rules validate config/rules/pilot/bank

# 2. Fire all four rules against the bundled fixture (positive + negative).
uv run bmr-rules fixture-run \
    --rules config/rules/pilot/bank \
    --fixture tests/bmr/fixtures/rules/fixtures/bpcr_weight_match.json

# 3. Diff a draft against the production bank (used by the authoring skill).
uv run bmr-rules diff config/rules/pilot/bank <your-draft.yaml>
```

Or end-to-end via the API: `POST /api/bmr/runs` with a package id whose
extraction.json has the relevant fields. The four rules will fan out across
the agents and produce findings in your run report.

---

## Walkthrough — pick one and read it top-to-bottom

### `same_page` — operator signature is present on each BPCR step page

[`bank/alcoa_attributable_operator_signature.yaml`](bank/alcoa_attributable_operator_signature.yaml)

```yaml
context_object:
  scope: same_page
source:
  field: operator_signature
  scope_hint: bpcr_step_page
```

Reads. Per page tagged `bpcr_step_page`, look up `operator_signature`. If
the value is empty/missing, emit a critical Attributable finding for that
page. **One finding per offending page.**

### `cross_document` — BPCR step weight matches the raw-material doc

[`bank/alcoa_accurate_bpcr_weight_match.yaml`](bank/alcoa_accurate_bpcr_weight_match.yaml)

```yaml
context_object:
  scope: cross_document
  role: RawMaterialPage
  entity_match:
    strategy: normalise
    aliases_file: backend/config/rules/pilot/aliases/materials.yaml
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

Reads. For each BPCR step page, normalise the material name (using the
alias table), find the matching raw-material page, compare the two
weights with ±0.1 kg tolerance. **One finding per mismatched step.**

### `page_aggregate` — sum of step weights matches the batch target

[`bank/alcoa_accurate_bpcr_step_sum.yaml`](bank/alcoa_accurate_bpcr_step_sum.yaml)

```yaml
context_object:
  scope: page_aggregate
  page_selector:
    document_role: BPCR
    page_filter: all_bpcr_step_pages
  aggregation: sum
source:
  field: dispensed_weight_kg
expected:
  field: batch_target_weight_kg
  document_ref_hint: BMR
tolerance:
  kind: percent
  value: 0.5
```

Reads. Sum `dispensed_weight_kg` across every BPCR step page. Compare to
`batch_target_weight_kg` on the BMR doc. ±0.5 % tolerance. **One finding
per run** (because the aggregation collapses to a single number).

### `checklist_synthesis` — per-step roll-up

[`bank/checklist_bpcr_step_complete.yaml`](bank/checklist_bpcr_step_complete.yaml)

```yaml
context_object:
  scope: checklist_synthesis
  group_by: bpcr_step
synthesises_from:
  - alcoa.accurate.bpcr-raw-material-weight-match
  - alcoa.attributable.operator-signature
```

Reads. Take the findings from the two leaf rules above, group them by BPCR
step number, emit one synthesised "step is/isn't complete" finding per
step. Reviewer sees a step-level verdict in addition to the per-rule rows.

---

## Diagnostic outputs

Each rule, when it fires, attaches a fixed evidence set so reviewers know
which page / value / rule it is talking about:

- `finding.rule_id` + `finding.rule_version` — the catalogue
- `finding.rule_content_hash` — stable across loads (numeric round-trip safe)
- `finding.evidence[]` — `(doc_id, page_index, field, value)` tuples
- `finding.tolerance_applied` — the exact kind / value / unit used
- `finding.severity` + `alcoa_tag` + `gmp_category` — for severity gating
- `finding.fallback_applied` — present only when a fallback policy fired

---

## When to add a new rule

1. Drop a YAML under `bank/`.
2. Run `uv run bmr-rules validate config/rules/pilot/bank` — fail loud if
   the schema rejects it.
3. Run `uv run bmr-rules fixture-run …` against a fixture you wrote with a
   known violation, confirm the rule fires.
4. Run the same against a clean fixture, confirm the rule does NOT fire.

The schema reference lives at `backend/config/rules/schema/rule.schema.v1.0.md`
(auto-generated from `rule.schema.v1.0.json`).
