# Compliance Rule Authoring Playbook

This guide is for domain owners and QA reviewers writing or updating compliance rules without changing backend code.

## Goals

- Keep rules deterministic and explainable.
- Minimize false positives from broad applicability.
- Make rule intent maintainable by non-authors.
- Preserve stable scoring across runs.

## Where to edit

- Rule text: `backend/app/compliance/rules/*_rules.md`
- Rule behavior/config: `backend/app/compliance/rules/*_rules.yaml`
- Document profile + section taxonomy: `backend/app/compliance/rules/document_profiles.yaml`

Rule behavior must live in YAML, not in comments.

## Authoring model

Each rule has two parts:

1. Human-readable statement (`.md`)
2. Machine-executable metadata (`.yaml`)

Minimum YAML fields to set for every new or changed rule:

- `applicable_document_types`
- `applicable_section_types`
- `applicable_page_types` (if needed)
- `pass_criteria`
- `skip_conditions`

Optional advanced fields:

- `evaluation_mode: cannot_evaluate`
- `cannot_evaluate_reason`
- `requires_external_data`
- `cross_section_requirements`
- `keywords` (only if helpful; avoid over-restricting)

## Section and document scoping

Use canonical values defined in `document_profiles.yaml`.

Examples:

- Document types: `batch_record`, `sop`, `logbook`, `certificate`
- Section types: `manufacturing_operations`, `material_dispensing`, `qc_report`, `line_clearance`

If a section name appears differently in documents, add alias mapping in `document_profiles.yaml` instead of inventing a new section type in a rule.

## Writing good `pass_criteria`

Use explicit, testable language:

- Good: "Any non-empty text in `Done by`/`Checked by` columns counts as signed."
- Bad: "Looks properly signed."

Include OCR-aware instructions where relevant:

- Garbled handwritten text may still be valid signature evidence.
- Dash values (`-`, `---`, `—`) may mean not applicable.
- OCR year artifacts should not be treated as hard data-integrity failures without context.

## Writing good `skip_conditions`

Skip should represent true non-applicability, not failure.

- Good: "Page has no checklist items -> not_applicable"
- Bad: "No checklist found -> non_compliant"

Prefer concrete conditions tied to page structure/content.

## When to use `cannot_evaluate`

Use this for rules that require data outside the packet:

- Training records
- Signature logs
- Calibration systems
- Archive/IT systems

Set all of:

- `evaluation_mode: cannot_evaluate`
- `cannot_evaluate_reason`
- `requires_external_data`

## Cross-section rules

For rules comparing two sections, set:

- `scope: document` or `scope: section`
- `cross_section_requirements` (from deterministic resolver)

Current requirement IDs include:

- `operation_vs_weighing_reconciliation`
- `material_usage_vs_dispensing`
- `sample_sent_vs_qc_report`
- `qc_vs_coa_consistency`
- `inter_section_consistency`

## Anti-patterns to avoid

- Broad rules with no document/section scope.
- Over-reliance on generic keywords as primary applicability logic.
- Encoding executable logic in comments only.
- Treating OCR artifacts as compliance failures by default.
- Mixing pass/fail criteria with business process assumptions not present in evidence.

## Change checklist

Before marking a rule update complete:

1. Rule text and YAML are both updated.
2. Document and section scopes are set.
3. `pass_criteria` and `skip_conditions` are explicit.
4. Any external dependency is marked `cannot_evaluate`.
5. Config validator passes.

Validator command:

```bash
backend/.venv/bin/python - <<'PY'
from app.compliance.rules.registry import get_registry
from app.compliance.rules.profiles import validate_compliance_configs
validate_compliance_configs(get_registry())
print("OK")
PY
```

## Quick templates

### Standard page rule

```yaml
12:
  applicable_document_types: [batch_record]
  applicable_section_types: [manufacturing_operations]
  applicable_page_types: [form]
  pass_criteria: >
    <explicit condition for compliance>
  skip_conditions:
    - "Page has no <required structure> -> not_applicable"
```

### External dependency rule

```yaml
21:
  evaluation_mode: cannot_evaluate
  cannot_evaluate_reason: "Requires calibration system records"
  requires_external_data: [calibration_records]
```

### Cross-section rule

```yaml
7:
  scope: document
  cross_section_requirements:
    - sample_sent_vs_qc_report
  pass_criteria: >
    <explicit comparison expectation>
```

## Ownership recommendation

- Domain owner: rule text + acceptance semantics.
- QA/compliance lead: severity + process correctness.
- Engineering owner: schema compliance + deterministic fit.

This keeps SoC clean while preserving fast iteration.
