# Research: Rule-Spec Schema & Authoring

**Feature**: 005 | **Spec Version**: v1

## R-1. JSON Schema Draft 2020-12

**Decision**: Use JSON Schema Draft 2020-12 for the rule schema, emitted from a
hand-authored source file (not generated from pydantic).

**Why**:
- Draft 2020-12 supports `$defs`, conditional subschemas (`if/then/else`), and
  `unevaluatedProperties: false` — all needed to express `context_object.scope`-dependent
  required fields (cross_document requires `role` + `entity_match`; page_aggregate
  requires `page_selector` + `aggregation`).
- Authoring the schema by hand (with tests) keeps it readable by rule authors reviewing
  the contract. Generated schemas from pydantic produce artefacts that are harder to read
  and version-control.
- The Python `jsonschema` library (>= 4.17) implements Draft 2020-12.

**Alternatives rejected**: OpenAPI-style schemas (overkill for the problem); pydantic →
JSON Schema auto-emit (coupling + readability cost).

## R-2. Versioned parallel validators

**Decision**: Each rule YAML declares `schema_version: "1.0"`. One file per version lives
at `backend/config/rules/schema/rule.schema.vX.Y.json`. The validator dispatches by the
rule's declared version and runs that specific schema.

**Why**:
- Guarantees reproducibility of prior audit runs: a rule authored against schema 1.0
  continues to validate against 1.0 even after 1.1 is released.
- Schema changes ship in a CHANGELOG.md with deprecation notes — authors migrate at their
  own pace.

**Alternatives rejected**: Always-latest with auto-migration (loses reproducibility; old
exports become un-reproducible); single forever-schema (infeasible — schema evolves).

## R-3. Schema shape — the top-level

**Decision**: Every rule YAML has the following top-level keys (abridged — full schema in
`contracts/rule-schema.md`):

```yaml
schema_version: "1.0"
id: string                          # stable, unique within rule bank
version: string                     # semver of THIS rule
severity: critical | major | minor | info
alcoa_tag: Attributable | Legible | Contemporaneous | Original | Accurate | Consistent | Enduring | Complete | Available
gmp_category: optional string       # for GMP rules
description: string                 # human-readable, 1–3 sentences
context_object: { ... }             # per Spec 003 §2.3
source: { field: string, scope_hint?: string }
target?: { field: string }          # required iff cross_document
expected?: { field: string, document_ref_hint?: string }
tolerance?: { kind: absolute|percent|relative, value: number, unit?: string }
multiplicity?: first | all | error
fallback?: flag_as_unevaluated | flag_as_indeterminate | treat_as_pass
synthesises_from?: list[rule_id]    # checklist synthesis (Spec 001)
```

**Why**: Mirrors exactly what Spec 003's runtime consumes; no surprises at load time.

## R-4. Error messages shaped for authors

**Decision**: Validator errors are shaped through a dedicated `error_formatter.py` that
converts raw `jsonschema` errors into author-facing messages:

- `path` → `rule_id + JSON pointer`
- `message` → human-readable ("Field `tolerance` is required because `source.field` is
  numeric, but `tolerance` was not declared.")
- `fix_hint` → a suggested YAML snippet.

**Why**: Raw jsonschema errors are cryptic. Rule authors are QA SMEs, not schema nerds.

**Alternatives rejected**: Raw jsonschema errors (unfriendly); generated LLM explanations
(non-deterministic).

## R-5. Skill architecture — three modes, one SKILL.md

**Decision**: One `SKILL.md` file with three modes (`author`, `tune`, `migrate`), each
with:
- A dedicated prompt file in `prompts/`.
- A template file in `templates/`.
- A declared input contract (NL description / corpus query / Python file).
- A declared output contract (YAML draft + validator report + fixture-run report).

The skill is invoked via Cursor's agent with a mode argument. It does NOT commit the
produced YAML; the author reviews and commits.

**Why**:
- Explicit modes keep prompts small, testable, and reviewable.
- No auto-commit matches Constitution VIII (author = accountable).

**Alternatives rejected**: Single "do anything" mode; skill auto-commits (risky).

## R-6. "Author" mode — NL → YAML

**Decision**: Author mode input:
- NL description of the rule
- Path to the manifest + aliases + existing rule bank (for reference)
- Optional: positive + negative fixtures
- Optional: author-declared severity / ALCOA tag / GMP category

The skill produces:
1. A YAML draft.
2. A `jsonschema` validation report.
3. If fixtures supplied, a fixture-run report with finding ids / evidence / pass|fail.
4. A "ready / not-ready" verdict.

If the NL description is ambiguous (e.g., "weight must match" — match what, to what
tolerance?), the skill MUST ask clarifying questions before emitting YAML.

**Why**: Determinism-leaning; author reviews output before commit.

## R-7. "Tune" mode — corpus-driven proposals

**Decision**: Tune-mode input: `rule_id`. The skill queries
`/api/v1/feedback/samples?rule_id=…` (Spec 004), inspects DISMISS reasons + observed vs
system values, and proposes specific schema changes:

- Many `ACCEPTABLE_VARIANCE` DISMISSes with a consistent delta ⇒ suggest increasing
  `tolerance.value`.
- Many `OCR_MISREAD` DISMISSes with a consistent entity name ⇒ suggest an alias addition.
- Many `RULE_MISCONFIGURED` DISMISSes ⇒ flag the rule for human review; do NOT auto-propose.

Output: YAML diff + rationale citing specific feedback sample ids.

**Why**: Closes the loop from Spec 004's corpus to Spec 003's rules. Makes the system
learn without being autonomous.

**Alternatives rejected**: Auto-apply tune proposals (risky; loses reviewer oversight);
ignore corpus (wastes signal).

## R-8. "Migrate" mode — Python SOP → YAML

**Decision**: Migrate-mode input: path to a legacy Python SOP rule file. The skill parses
the file (heuristic: look for a `check(record) -> bool | list[Finding]` signature), maps
conditionals to `context_object` + `tolerance` + `fallback`, and emits a YAML draft.
Logic that cannot be expressed in the schema produces an annotated TODO in the draft and
a callout in the report.

**Why**: Reduces the migration burden for teams with existing Python rule banks, while
being honest about expressivity gaps.

**Alternatives rejected**: Auto-migrate everything silently (bug magnet); reject
migration as too hard (loses a big adoption lever).

## R-9. Fixture validation — reuse Spec 001/003 runner

**Decision**: The skill and the CLI (`bmr-rules fixture-run`) both call into
`backend/app/tools/fixture_runner.py`, which in turn invokes the same rule engine entry
point the pipeline uses. Results compared deterministically against expected fixture
outcomes.

**Why**: If the skill's fixture run uses a different engine than the pipeline, authors
will ship rules that "pass" the skill but misfire at runtime. Single-source the runner.

## R-10. Schema-parity CI gate

**Decision**: A CI test (`tests/tools/test_schema_parity.py`) loads the schema path the
backend loader uses (`backend/config/rules/schema/rule.schema.v1.0.json`) and asserts
byte-equality with the path the skill references. Drift fails the build.

**Why**: Constitution VII — the authoring surface and the runtime surface MUST agree.

## R-11. No LLM in the validator path

**Decision**: The `bmr-rules validate` CLI and the backend loader MUST NOT call any LLM.
They are pure schema + deterministic checks. The skill uses an LLM to author YAML, but
once YAML exists the validator path is deterministic.

**Why**: Rule validation is a correctness boundary; non-determinism here poisons
reproducibility.

## R-12. Test strategy

- **Schema unit tests**: positive + negative fixtures under `tests/tools/fixtures/rules/`.
- **Validator error-shaping tests**: assert author-facing error text for every common
  failure (missing tolerance, unknown strategy, bad aliases path).
- **CLI tests**: `bmr-rules validate path/to/rule.yaml` and `bmr-rules fixture-run
  path/to/rule.yaml --positive fx_a.json --negative fx_b.json`.
- **Schema-parity test**: Constitution VII gate.
- **Skill quickstart** in `quickstart.md` walks the author through each mode manually.
