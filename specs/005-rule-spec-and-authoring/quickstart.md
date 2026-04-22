# Quickstart: Rule-Spec Schema & Authoring

**Feature**: 005 | **Spec Version**: v1

Walks through the three skill modes (author / tune / migrate) and the `bmr-rules` CLI.

## Prerequisites

- Backend + Postgres running with Specs 001–004 merged.
- `backend/config/rules/schema/rule.schema.v1.0.json` present.
- Cursor open at the repo root, with the `bmr-rule-author` skill loaded (present at
  `.cursor/skills-cursor/bmr-rule-author/SKILL.md`).
- `uv run python -m pip install -e backend/` (so `bmr-rules` is on PATH).

## 1. CLI sanity — validate the existing pilot bank

```bash
bmr-rules validate backend/config/rules/pilot/
```

Expect:
```
backend/config/rules/pilot/alcoa/bpcr-step-initials-present.yaml  OK
backend/config/rules/pilot/alcoa/bpcr-raw-material-weight-match.yaml  OK
...
142 rules validated, 0 errors, 0 warnings.
```

Exit code `0`. Any failure here blocks the rest of the flow.

## 2. Author mode — NL → validated YAML

Open the skill in Cursor, invoke with mode `author`. Provide:

- NL description:
  > "On BPCR step 5, the equipment-id recorded must match an equipment-id present in the
  > Equipment Log for this batch. If no match, it's an ALCOA-Attributable Major finding.
  > Case and whitespace don't matter."

- `manifest_path`: `backend/config/bmr/pilot-manifest.yaml`
- `positive_fixture_path`: `backend/tests/fixtures/rules/positive/missing-equipment-id.json`
- `negative_fixture_path`: `backend/tests/fixtures/rules/negative/all-equipment-ids-match.json`

Expect the skill to:

1. Ask clarifying questions if anything is ambiguous (e.g., "any tolerance for trailing
   whitespace beyond normalisation? [y/N]").
2. Emit YAML to `backend/config/rules/pilot/alcoa/bpcr-equipment-id-match.yaml`:

```yaml
schema_version: "1.0"
id: alcoa.attributable.bpcr-equipment-id-match
version: 1.0.0
severity: major
alcoa_tag: Attributable
description: "BPCR equipment-id must match an id in the Equipment Log for this batch."
context_object:
  scope: cross_document
  role: EquipmentLog
  entity_match:
    strategy: normalise
    normalise: true
    case_insensitive: true
    punctuation_strip: ["-","_","."]
  multiplicity: first
  fallback: flag_as_unevaluated
source: { field: equipment_id, scope_hint: bpcr_step_page }
target: { field: equipment_id }
```

3. Run validator: `validation_report.errors == []`.
4. Run fixtures: positive fires 1 finding, negative fires 0 findings.
5. Verdict: `ready`.

If any step fails, the skill surfaces the report and does NOT mark `ready`.

## 3. Drop the rule into the pipeline

```bash
bmr-rules validate backend/config/rules/pilot/alcoa/bpcr-equipment-id-match.yaml
# OK

# Restart backend
cd backend && uv run uvicorn app.main:app --reload
```

Startup log includes the new rule in the rule count. Run a pilot package; verify the
rule fires when expected.

## 4. Tune mode — propose a tolerance bump from feedback corpus

Assume reviewers have been dismissing `bpcr-raw-material-weight-match` findings with
`ACCEPTABLE_VARIANCE` when the delta is within 0.15 kg.

Invoke the skill with mode `tune`:
- `rule_id`: `alcoa.accurate.bpcr-raw-material-weight-match`
- `rule_yaml_path`: the rule
- `feedback_samples_api`: `http://localhost:8000/api/v1/feedback/samples`

Expect the skill to:

1. Query the corpus; count samples in the last 30 days; require ≥ 10 to proceed.
2. Inspect observed vs system values; find that the consistent delta is ~0.13 kg.
3. Emit a proposal:

```
Proposal 1/1 — tolerance bump
  current: { kind: absolute, value: 0.1, unit: kg }
  proposed: { kind: absolute, value: 0.15, unit: kg }
  rationale: 18 of 24 ACCEPTABLE_VARIANCE dismissals in the last 30 days had a delta
             <= 0.15 kg. Widening the tolerance would eliminate these dismissals while
             still catching the 6 outliers (delta 0.3–0.6 kg).
  supporting_samples: [fbs_01H..., fbs_01H..., ..., fbs_01H...]
  diff:
    -    tolerance: { kind: absolute, value: 0.1, unit: kg }
    +    tolerance: { kind: absolute, value: 0.15, unit: kg }

Verdict: proposals_ready
```

4. The skill writes the diff to a temp path and stops. The author reviews and, if
   approved, applies the diff via normal git workflow.

## 5. Migrate mode — legacy Python → YAML

Given a legacy rule file (sample `backend/legacy/rules/step3_weight_match.py`), invoke
mode `migrate`:

- `python_source_path`: `backend/legacy/rules/step3_weight_match.py`
- `manifest_path`: pilot manifest

Expect:

1. YAML draft written to `backend/config/rules/pilot/migrated/step3-weight-match.yaml`.
2. `migration_report.verdict = ready` if every Python branch mapped cleanly.
3. If any branch references a lookup the schema cannot express (e.g., "regex match on
   batch id" without a strategy), the report flags `schema_gap: true` and the verdict is
   `needs_human`.

Validate + fixture-run the draft via CLI:

```bash
bmr-rules validate backend/config/rules/pilot/migrated/step3-weight-match.yaml
bmr-rules fixture-run backend/config/rules/pilot/migrated/step3-weight-match.yaml \
  --positive=backend/tests/fixtures/rules/positive/ \
  --negative=backend/tests/fixtures/rules/negative/
```

## 6. Schema-parity CI check

```bash
cd backend && uv run pytest tests/tools/test_schema_parity.py -v
```

Must pass. If it fails, something references an outdated schema path; fix and re-run.

## 7. Negative cases — schema refusing bad rules

Create a bad rule:

```yaml
# bad-rule.yaml
schema_version: "1.0"
id: alcoa.accurate.bad-rule
version: 1.0.0
severity: major
alcoa_tag: Accurate
description: "weight match"
context_object:
  scope: cross_document
  role: RawMaterialPage
  # ^^ missing entity_match — schema error
source: { field: dispensed_weight_kg }
tolerance: { kind: absolute, value: -0.1 }   # negative value — schema error
```

```bash
bmr-rules validate /tmp/bad-rule.yaml
```

Expect:

```
/tmp/bad-rule.yaml  FAIL
  [alcoa.accurate.bad-rule] /context_object: missing required key "entity_match"
      context_object.scope=cross_document requires entity_match.
      fix: add:
        entity_match:
          strategy: normalise
          aliases_file: backend/config/rules/pilot/aliases/materials.yaml
  [alcoa.accurate.bad-rule] /tolerance/value: -0.1 is not > 0
      tolerance.value must be positive.
      fix: use a positive number (e.g., 0.1).
Exit 1.
```

## 8. Versioning — pin a rule to v1.0

Add schema v1.1 in the future with a new optional field. Existing rules with
`schema_version: "1.0"` continue to validate against v1.0 unchanged. New rules MAY opt
into v1.1 by declaring `schema_version: "1.1"`.

## 9. Constitution spot-checks

- Rule-as-data (IX): every pilot rule YAML has a `context_object` block (validator pass
  is necessary + sufficient).
- Existing framework backbone (VII): `backend/app/rules/loader.py` references the same
  schema path the skill references; schema-parity test passes.
- ALCOA+ (VIII): every rule change arrives via `git log` with author + timestamp; no
  auto-commits.
