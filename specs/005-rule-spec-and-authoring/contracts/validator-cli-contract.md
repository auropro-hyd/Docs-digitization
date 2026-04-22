# Validator CLI Contract: `bmr-rules`

**Feature**: 005 | **Version**: v1 | **Module**: `backend/app/tools/rules_cli.py`

The `bmr-rules` CLI is a deterministic, LLM-free tool shipped with the backend. It
exposes the same schema and fixture-running machinery the skill uses, callable from
terminals, pre-commit hooks, and CI.

## Subcommand: `validate`

```bash
bmr-rules validate <path> [--schema-path=...] [--format=human|json]
```

- `<path>` — a single rule YAML OR a directory of rule YAMLs.
- `--schema-path` — override (defaults to `backend/config/rules/schema/rule.schema.vX.Y.json`
  per each rule's declared `schema_version`).
- `--format=json` — machine-readable; emits `ValidationReport[]`.

**Exit codes**:
- `0` — all rules valid.
- `1` — one or more rules invalid (blocking).
- `2` — CLI usage error.

**Invariants**:
- The CLI MUST NOT call any LLM, network, or DB.
- The CLI MUST print one error per issue with a JSON pointer into the rule YAML.
- Author-facing error text is produced by the shared `error_formatter.py`; same output as
  the skill produces.

## Subcommand: `diff`

```bash
bmr-rules diff <old-path> <new-path> [--format=human|json]
```

Compare two rule YAMLs and emit a semantic diff (schema-aware, not line-based). Useful in
PR review and in the skill's tune-mode output.

**Invariants**:
- Detects field additions/removals, enum changes, tolerance shifts.
- Classifies changes as `breaking` / `backward-compatible` / `cosmetic`.

## Subcommand: `fixture-run`

```bash
bmr-rules fixture-run <rule-path> \
  --positive=<fixture-json-or-dir> \
  --negative=<fixture-json-or-dir> \
  [--format=human|json]
```

Execute the rule against provided fixtures using the production rule engine entry point
(`backend/app/rules/engine.py`).

**Exit codes**:
- `0` — positive fixtures fire, negative fixtures do not fire, and required evidence refs
  are present.
- `1` — any fixture mismatch.
- `2` — CLI usage error.

**Output**: `FixtureRunReport` (per `data-model.md §2.3`).

**Invariants**:
- Uses the same rule engine as the pipeline; no parallel evaluator.
- Loads aliases / manifest from paths declared in the rule itself.

## Subcommand: `migrate` (thin)

```bash
bmr-rules migrate <python-source-path> [--out=<yaml-path>]
```

Thin wrapper for CI-style usage. The rich migration flow lives in the `bmr-rule-author`
skill; this CLI command is a terminal affordance that calls the same underlying
migrator and produces a `MigrationReport` JSON, without interactive clarifications.

## Global flags

- `--verbose` — show full jsonschema traceback (debug).
- `--color=auto|always|never`.
- `--version` — print CLI version + schema versions detected in `backend/config/rules/schema/`.

## CI integration

Recommended:

```bash
# pre-commit
bmr-rules validate backend/config/rules/pilot/ --format=json > /dev/null

# PR gate
bmr-rules validate backend/config/rules/pilot/
bmr-rules fixture-run backend/config/rules/pilot/alcoa/some-new-rule.yaml \
  --positive=backend/tests/fixtures/rules/positive/ \
  --negative=backend/tests/fixtures/rules/negative/
```

## Schema-parity test

`backend/tests/tools/test_schema_parity.py` asserts:

```python
loader_schema_path = Path("backend/config/rules/schema/rule.schema.v1.0.json")
skill_schema_path  = Path(".cursor/skills-cursor/bmr-rule-author/prompts/..._references_schema_at").read_text()
assert loader_schema_path.read_bytes() == Path(skill_schema_path).read_bytes()
```

Fails CI on drift (Constitution VII gate).
