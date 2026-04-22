# Skill Contract: `bmr-rule-author`

**Feature**: 005 | **Version**: v1 | **Location**: `.cursor/skills-cursor/bmr-rule-author/SKILL.md`

Defines the inputs, outputs, and behaviour contract for the three skill modes.

## Common

- The skill is invoked inside Cursor with a mode argument (`author` | `tune` | `migrate`).
- It reads the rule schema at `backend/config/rules/schema/rule.schema.v1.0.json` and
  the current rule bank at `backend/config/rules/pilot/**/*.yaml`.
- It produces a YAML draft + validation report + fixture-run report. It NEVER commits; it
  NEVER modifies the backend source tree outside the YAML draft path the author approves.
- If the skill cannot safely proceed (ambiguous NL, missing fixtures, unresolved gaps), it
  MUST ASK, not guess.

## Mode: `author`

### Inputs

| Name | Type | Required | Notes |
|---|---|---|---|
| `nl_description` | string | yes | reviewer-style plain language rule description |
| `manifest_path` | path | yes | e.g. `backend/config/bmr/pilot-manifest.yaml` |
| `aliases_dir` | path | no | e.g. `backend/config/rules/pilot/aliases/` |
| `existing_rules_path` | path | no | the rule bank root (for style / naming conformity) |
| `positive_fixture_path` | path | no | JSON or dir with planted failures |
| `negative_fixture_path` | path | no | clean JSON or dir |
| `author_hints` | object | no | e.g. `{ severity: major, alcoa_tag: Accurate }` |

### Output

1. `draft_yaml` — path where the draft was written (author-approved).
2. `validation_report` — per `data-model.md §2.1`.
3. `fixture_run_report` — per `data-model.md §2.3` (if fixtures supplied).
4. `verdict` — `ready` / `needs_input` / `not_ready`.

### Invariants

1. The draft MUST validate against `rule.schema.v1.0.json` before the skill ends its
   turn; if validation fails, the skill MUST iterate on the draft (bounded attempts) and
   surface unresolved errors.
2. If fixtures were supplied, `fixture_run_report.verdict` MUST appear in the skill's
   output; `ready` is only possible if fixtures pass.
3. The skill MUST reject its own output when critical fields are not derivable from the
   NL description (e.g., "match within a tolerance" without a value). It asks instead.
4. The skill MUST emit ONE YAML rule per invocation. Batch authoring is not supported in
   v1.

## Mode: `tune`

### Inputs

| Name | Type | Required | Notes |
|---|---|---|---|
| `rule_id` | string | yes | |
| `rule_yaml_path` | path | yes | the rule being tuned |
| `feedback_samples_api` | url | yes | Spec 004 `/api/v1/feedback/samples` base |
| `min_samples` | int | no | default 10; below this, skill refuses |
| `observation_window_days` | int | no | default 30 |

### Output

1. `tune_proposals` — zero or more `TuneProposal` (per `data-model.md §2.4`).
2. `summary` — plain text; which fields would change, why, which samples support each
   proposal.
3. `draft_yaml_diff` — unified diff if proposals applied; NOT applied by the skill.
4. `verdict` — `proposals_ready` / `insufficient_data` / `requires_human_review`.

### Invariants

1. Proposals MUST cite `feedback_sample_id` values; uncited proposals are rejected by the
   skill's own self-check.
2. If `RULE_MISCONFIGURED` is the dominant reason, the skill MUST emit
   `requires_human_review` and NOT propose schema edits.
3. The skill MUST NOT propose changes that violate the schema (e.g., negative tolerance,
   unknown strategy). Its output is pre-validated against the schema.

## Mode: `migrate`

### Inputs

| Name | Type | Required | Notes |
|---|---|---|---|
| `python_source_path` | path | yes | the legacy Python rule file |
| `manifest_path` | path | yes | |
| `aliases_dir` | path | no | |
| `rule_id_hint` | string | no | skill falls back to filename-derived id |

### Output

1. `draft_yaml` — path where the draft was written.
2. `migration_report` — per `data-model.md §2.5`.
3. `verdict` — `ready` / `needs_human`.

### Invariants

1. If any conditional branch in the Python source cannot be mapped to a supported
   `context_object` + `tolerance` combination, the migration report MUST list it as a
   `schema_gap: true` TODO, and the verdict is `needs_human`.
2. The skill MUST NOT silently drop logic from the source.
3. The migrated YAML MUST carry `schema_version: "1.0"` and MUST schema-validate.

## Observability

- The skill logs (to `.cursor/skills-cursor/bmr-rule-author/logs/`):
  - invocation mode + inputs (redact secrets)
  - number of schema-validation attempts
  - number of clarifying questions asked
  - final verdict
- Logs are author-local; not shipped to backend telemetry.

## Failure modes

| Condition | Behaviour |
|---|---|
| NL description ambiguous on a required field | Ask a clarifying question; do NOT emit YAML |
| Schema parse failure | Halt with error pointing at `rule.schema.v1.0.json` |
| Fixtures missing and the author requested `ready` | Emit `not_ready` with the reason "no fixtures provided" |
| `tune` with `samples < min_samples` | Emit `insufficient_data`; do NOT propose |
| `migrate` with logic gap | Annotated TODO + `needs_human` verdict |
