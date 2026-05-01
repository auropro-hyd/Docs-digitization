---
name: audit-rule-author
description: >
  Author and maintain compliance audit rule configuration files for the Docs-digitization
  pipeline. Use this skill whenever the user wants to: add a new rule to an existing audit
  framework, update an existing rule's config or pass criteria, reduce false positives from
  a rule, create an entirely new audit framework (new rule set), tune skip conditions or
  severity, configure cannot_evaluate for rules needing external data, or change a rule's
  scope or applicable document/section types. Triggers on phrases like: "add a rule",
  "update rule", "create a new audit", "write a compliance check", "too many false positives",
  "rule is firing incorrectly", "add a new framework", "configure audit rules",
  "author compliance rules", "new audit type".
---

# Audit Rule Authoring

You help author and maintain compliance audit rule configuration files for the
Docs-digitization pipeline. Rules live in `backend/app/compliance/rules/`.

**Read `references/rule_authoring_playbook.md` now** — it is the authoritative guide for
all authoring conventions. Follow it exactly. This SKILL.md only covers the workflow
orchestration on top of it.

---

## Determine the task

Ask the user one question to orient yourself if it isn't already clear:

> **"Are you (a) adding or updating rules in an existing audit framework, or (b) creating a brand-new audit framework?"**

Then follow the appropriate path below.

---

## Path A: Add or update rules in an existing framework

### 1. Read context files

Read all three before touching anything:

```
backend/app/compliance/rules/<framework>_rules.md
backend/app/compliance/rules/<framework>_rules.yaml
backend/app/compliance/rules/document_profiles.yaml   ← canonical document/section types
```

The `document_profiles.yaml` is the source of truth for all valid `applicable_document_types`
and `applicable_section_types` values. Never invent type names — use only what is defined there.

### 2. Author the rule(s)

Follow the authoring conventions in `references/rule_authoring_playbook.md`:
- Rule text goes in `<framework>_rules.md`
- YAML config goes in `<framework>_rules.yaml`
- Use the minimum required fields plus any applicable optional fields

### 3. Validate

```bash
cd <project_root>
backend/.venv/bin/python - <<'PY'
from app.compliance.rules.registry import get_registry
from app.compliance.rules.profiles import validate_compliance_configs
validate_compliance_configs(get_registry())
print("OK")
PY
```

Fix any errors before reporting done.

---

## Path B: Create a new audit framework

A new framework requires three files. Create them in this order:

### 1. Register the framework

Add an entry to `backend/app/compliance/rules/agents_meta.json`:

```json
{
  "id": "<framework_id>",
  "label": "<Human-readable label>",
  "description": "<One-sentence description of what this framework audits>"
}
```

`framework_id` must be lowercase, no spaces (e.g., `data_integrity`, `environmental`).

### 2. Create the rule text file

Create `backend/app/compliance/rules/<framework_id>_rules.md`.

Structure it with category sections matching the framework's logical groupings.
Each rule is one numbered line: `<number>. <Imperative statement of the GMP requirement.>`

```markdown
Category: <Category Name>

1. <Rule statement.>
2. <Rule statement.>

--------------------------------------------------

Category: <Next Category>

3. <Rule statement.>
```

### 3. Create the YAML config file

Create `backend/app/compliance/rules/<framework_id>_rules.yaml`.

Start from this skeleton — fill in what's known, leave optional fields at defaults:

```yaml
defaults:
  scope: page
  severity: observation
  evaluation_mode: llm
  applicable_document_types: []
  excluded_document_types: []
  applicable_page_types: []
  applicable_section_types: []
  cross_section_requirements: []
  keywords: []
  pass_criteria: ""
  skip_conditions: []
  cannot_evaluate_reason: ""
  requires_external_data: []
  notes: ""

categories:
  <category_slug>:
    severity: <major|minor|critical|observation>
    applicable_page_types: []
    rules:
      1:
        pass_criteria: >
          <explicit condition>
        skip_conditions:
          - "<condition> -> not_applicable"
```

Read `document_profiles.yaml` for valid document and section type values.

### 4. Validate

Run the validator (same command as Path A). Since the new agent class doesn't exist yet,
the validator may warn about the unregistered agent — that is expected. Note it to the user:
**agent class wiring is a separate engineering task** beyond this skill's scope.

---

## Pre-flight checklist (both paths)

- [ ] Rule text in `.md` matches YAML entries (same numbers, same count)
- [ ] Document and section types use canonical values from `document_profiles.yaml`
- [ ] `pass_criteria` is explicit and testable (see playbook for what "explicit" means)
- [ ] `skip_conditions` represent true non-applicability, not failure
- [ ] External dependencies use `cannot_evaluate` with reason + data list
- [ ] Config validator passes (or expected warnings are explained)
