# BMR Rule Schema Changelog

All notable changes to the BMR audit rule schema are documented here. The schema uses semantic versioning (`MAJOR.MINOR`). Minor bumps are additive; major bumps are breaking.

Consumers:

- `backend/app/bmr/rules/loader.py` — runtime loader for BMR pipeline.
- `backend/app/bmr/cli/__main__.py` (`bmr-rules validate`) — CI / pre-commit / author tool.
- `.cursor/skills-cursor/bmr-rule-author/SKILL.md` — authoring skill.

Every rule YAML MUST declare `schema_version`. Rules pinned to an older version continue to validate against that version after a minor schema bump (backward compatibility invariant).

---

## 1.0 — 2026-04-17

Initial published schema. Establishes the contract for:

- Top-level rule identity (`id`, `version`, `severity`, `alcoa_tag`, `description`).
- `context_object` block with three scopes (`same_page`, `cross_document`, `page_aggregate`).
- `entity_match` strategies (`exact`, `normalise`, `alias`, `step_number`, `batch_id`, `custom`) and `aliases_file` references.
- `page_selector` (`all_bpcr_step_pages`, `first_page`, `last_page`, `by_index`, `by_tag`) and aggregations (`sum`, `count`, `min`, `max`, `avg`).
- `tolerance` with positive `value` and three kinds (`absolute`, `percent`, `relative`).
- `multiplicity` (`first` | `all` | `error`) and `fallback` (`flag_as_unevaluated` | `flag_as_indeterminate` | `treat_as_pass`).
- Conditional requirements: `cross_document` requires `target` + `role` + `entity_match`; `page_aggregate` requires `page_selector` + `aggregation`; `same_page` forbids cross-scope keys.

### Added — deprecation (Spec 005 FR-013)

- Optional `deprecated` (boolean) — when `true`, the loader accepts the rule so prior runs continue to validate, but the compliance stage skips it so the rule no longer pollutes new runs.
- Optional `superseded_by` (string) — informational pointer to the replacement rule identity (typically `<rule_id>@<version>`). Not required when `deprecated: true`, but strongly recommended.

Both fields are additive and do not require bumping the schema major version — rules authored before the addition continue to validate unchanged.

---

## 1.1 — 2026-04-29

Additive over 1.0 (Spec 007 — BPCR layout-aware section detection).

- `page_selector.section_id` (optional string, slug pattern `^[a-z][a-z0-9_]*$`) — restrict the page selector to pages whose detected `section_id` matches. Populated post-extraction by the BPCR section tagger; pages with no section assignment never match.
- The literal `unsectioned` is reserved for the detector's filler spans and is rejected by the schema (`not: { const: "unsectioned" }`) so authors cannot accidentally target the sentinel.

The change is purely additive. v1.0 rules MUST continue to validate against the v1.0 schema unchanged. The loader picks the schema by the rule's pinned `schema_version`; v1.0 and v1.1 schemas both ship in the repo.

When `section_id` is set on a rule but the run produced no section assignments (detection disabled or failed), the engine applies the rule's existing `fallback` policy — no new fallback kind is introduced.
