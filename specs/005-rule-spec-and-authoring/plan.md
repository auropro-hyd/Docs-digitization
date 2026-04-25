# Implementation Plan: Rule-Spec Schema & Authoring

**Branch**: `005-rule-spec-and-authoring` | **Date**: 2026-04-17 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/005-rule-spec-and-authoring/spec.md`

## Summary

Operationalise Constitution IX (rule-as-data). Publish a versioned JSON Schema for rule
YAMLs (including Spec 003's `context_object` block). Ship a Cursor skill
(`bmr-rule-author`) with three modes:

- **author** — natural-language description → validated YAML rule.
- **tune** — feedback-corpus-driven proposals to change a rule's tolerance, aliases, or
  fallback.
- **migrate** — legacy Python SOP rule → YAML rule (one-shot assisted migration).

The skill is deterministic-leaning: it emits YAML, validates against the schema, runs the
draft against fixtures, and reports pass/fail. It never commits without the author's
approval.

No runtime engine code changes (Spec 003 already consumes the schema). This spec owns the
schema, the skill, and the validator CLI.

## Technical Context

**Language/Version**: Python 3.11+ (validator, CLI); the skill itself is a Cursor SKILL.md
under `.cursor/skills-cursor/bmr-rule-author/`.
**Primary Dependencies**: `jsonschema` (Python) for validation, `pyyaml` for load,
existing rule loader entry points, existing fixture runners from Specs 001/003. The skill
leverages Cursor's agent surface for NL → YAML authoring.
**Storage**: Schema JSON files in `backend/config/rules/schema/` (git-tracked). No DB
tables. Feedback corpus is read-only from Spec 004's `feedback_sample` table via the
`/api/v1/feedback/samples` endpoint.
**Testing**: pytest for schema + validator + CLI; a Playwright-like manual recipe in
`quickstart.md` for the skill flow (skills aren't unit-tested in the same way code is,
but the CLI they wrap IS).
**Target Platform**: Dev environment (skill runs in Cursor); CLI ships with backend image.
**Project Type**: Schema + CLI + skill. No API surface changes (skill consumes Spec 004's
`/feedback/samples` endpoint).
**Performance Goals** (from spec SC-*):
- Schema validation on a 200-rule bank ≤ 2 s.
- Skill "author" mode end-to-end (NL → validated + fixture-tested YAML) ≤ 60 s p95 for
  author-provided fixtures.
- Skill "tune" mode proposal generation ≤ 30 s p95 against a 1 000-sample corpus.
**Constraints**:
- Schema is the single source of truth for rule shape; Spec 003's loader and this spec's
  validator MUST load the same schema file. No duplicated schema definitions anywhere.
- Skill MUST NOT guess values. If the NL description is ambiguous, the skill MUST ask
  clarifying questions, not hallucinate.
- Skill MUST run fixture validation before declaring a rule "ready". Rules that fail
  positive-fixture firing OR trip the negative fixture are reported, not silently emitted.
- `migrate` mode emits a YAML draft that compiles; if the Python SOP rule has logic the
  schema cannot express, the skill MUST annotate the draft with a TODO and surface the
  gap in its report rather than faking coverage.
**Scale/Scope**: Pilot bank ~150 rules; growth expected to ~500–1 000 over 12 months. One
Cursor skill; one CLI command with three subcommands.

## Constitution Check

Reference: `.specify/memory/constitution.md` (v1.1.0).

- [x] **I. Leverage-first**: Reuses Spec 003's rule loader, Spec 001's fixture runner,
  Spec 004's feedback corpus API. New code: schema files + a small validator CLI. The
  skill itself is a Markdown SKILL + prompts.
- [x] **II. 5-stage soft gates**: This spec is authoring-side, not runtime. It sits
  outside the 5-stage pipeline entirely.
- [x] **III. Capability-first**: The validator CLI is a single-purpose, composable tool.
  No new runtime capabilities introduced.
- [x] **IV. Single final checkpoint**: Authoring is a pre-runtime activity; it does not
  create mid-pipeline HITL. The skill's "ready" report is read by the human author, not by
  the pipeline.
- [x] **V. Evidence-bound**: The skill's fixture-run report MUST cite the finding(s) the
  draft rule produced, by finding id, scope, and evidence refs, so the author can
  visually confirm correctness.
- [x] **VI. Configurable framework**: This spec IS the configurability surface. Schema +
  YAML rules are the contract; code changes are not required to add a rule.
- [x] **VII. Existing framework backbone**: The schema describes the rule shape Spec 003's
  loader already consumes; no divergence. CI test asserts `jsonschema.validate(rule,
  schema_loaded_by_backend) == jsonschema.validate(rule, schema_loaded_by_skill)`.
- [x] **VIII. ALCOA+ audit trail**: Rule YAMLs live in git; every change is tracked with
  author, timestamp, and diff via commits. The skill does NOT auto-commit; the author
  reviews and commits.
- [x] **IX. Rule-as-data**: This is the spec that makes IX real. `context_object` is a
  first-class schema field; tolerances, aliases, fallbacks are schema fields.

No violations.

## Project Structure

```text
specs/005-rule-spec-and-authoring/
├── spec.md
├── plan.md                       # this
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── rule-schema.md            # human-readable schema reference
│   ├── skill-contract.md         # inputs/outputs/modes for bmr-rule-author
│   └── validator-cli-contract.md # the `bmr-rules` CLI
└── checklists/requirements.md
```

```text
backend/
├── config/rules/
│   ├── schema/
│   │   ├── rule.schema.v1.0.json         # JSON Schema Draft 2020-12
│   │   ├── rule.schema.v1.1.json         # future; reserved slot + migration notes
│   │   └── CHANGELOG.md                  # human-readable schema changelog
│   └── pilot/                             # EXISTING rule bank
├── app/tools/
│   ├── __init__.py
│   ├── rules_cli.py                      # NEW: `bmr-rules validate|diff|fixture-run`
│   ├── validator.py                      # NEW: load + validate + error shaping
│   └── fixture_runner.py                 # NEW: reuse Spec 001/003 fixtures
└── tests/tools/
    ├── test_schema_v1_0.py               # schema happy + negative cases
    ├── test_validator_errors.py          # error messages are author-readable
    ├── test_cli_validate.py
    ├── test_cli_fixture_run.py
    └── fixtures/rules/
        ├── valid_cross_doc.yaml
        ├── valid_page_aggregate.yaml
        ├── missing_tolerance.yaml        # negative
        ├── unknown_schema_version.yaml   # negative
        └── alias_path_not_found.yaml     # negative

.cursor/skills-cursor/bmr-rule-author/
├── SKILL.md                              # ALREADY CREATED — refine with mode details
├── templates/
│   ├── cross_doc_rule.yaml.tmpl
│   ├── page_aggregate_rule.yaml.tmpl
│   └── same_page_rule.yaml.tmpl
└── prompts/
    ├── author.md
    ├── tune.md
    └── migrate.md
```

**Structure Decision**: Schema + CLI live in backend; the skill lives in
`.cursor/skills-cursor/bmr-rule-author/`. No frontend, no new API. Skill-side prompts
and templates are part of this spec's deliverable.

## Complexity Tracking

| Item | Why | Simpler Alternative Considered |
|---|---|---|
| Three skill modes (author/tune/migrate) instead of one | Each mode has a distinct input shape (NL string / corpus query / Python file) and distinct success criteria. A single mode would sprawl in its prompt and dilute deterministic guarantees. | Single "ask anything" mode. Rejected: opaque behaviour, poor reproducibility, awkward prompts. |
| Versioned schema with a CHANGELOG and parallel validators | Rule authors commit to a rule bank that outlives any one schema version. Versioning is what makes schema evolution safe without invalidating old exports. | Single always-latest schema with auto-migration. Rejected: breaks reproducibility of prior audit runs. |
| Skill requires fixtures before declaring "ready" | Rules that look right but misfire at runtime are the single most common authoring failure mode. Gating on fixture-run closes that loop. | Ship without fixture validation, rely on pipeline smoke tests. Rejected: delays feedback to the author and pollutes the feedback corpus. |

## Post-Design Constitution Re-Check

- [x] **I**: Validator reuses `jsonschema`; fixture runner reuses Spec 001/003 entry
  points. No parallel rule engine.
- [x] **II**: Authoring-only; no pipeline stage impact.
- [x] **III**: CLI commands are atomic: `validate`, `diff`, `fixture-run`.
- [x] **IV**: Skill "ready" report is author-facing; no mid-pipeline HITL injected.
- [x] **V**: Fixture-run report cites finding evidence; template enforces this structure
  (see `contracts/skill-contract.md §Output`).
- [x] **VI**: Schema IS the configuration surface; no Python encoding of rule semantics
  beyond the enum + field names.
- [x] **VII**: CI asserts schema equality between backend loader and skill validator
  (`test_schema_parity.py`).
- [x] **VIII**: No silent commits; author reviews + commits rule YAML.
- [x] **IX**: Schema formalises `context_object`, `tolerance`, `aliases_file`,
  `multiplicity`, `fallback`.

All 9 gates green after Phase 1.
