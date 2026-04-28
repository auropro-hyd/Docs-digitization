# Rule Bank — Reference Exemplars (post `document_profiles` overhaul)

## State of the bank

The rule bank is **deliberately almost empty** while it is repopulated
against the new `document_profiles.yaml` taxonomy (Akhilesh's
2026-04-28 overhaul). The 170 historical rules across ALCOA / GMP /
SOP / Checklist / Reconciliation referenced document and section
types that no longer exist in the new profile (`logbook`, `sop`,
`automated_systems`, `balance_printout`, `clean_room`,
`electronic_records`, `oos_investigation`, `oot_investigation`,
`retention_sample`).

Rather than half-update each rule by hand, the historical files are
kept on disk as text-only reference (`*_rules.archived.{md,yaml}`)
and a minimal set of exemplars seeds the new bank. The rule-author
agent skill repopulates the rest from the archived text.

## The three evaluation patterns

Every compliance rule we author falls into exactly one of these
patterns. The rule bank should contain at least one canonical
exemplar of each so the rule-author skill has a reliable template.

| # | Pattern | Verdict scope | Where it lives | Exemplar |
|---|---|---|---|---|
| 1 | **Same-page individual** | One rule, one page, evidence from a single field/row | `alcoa_rules.{md,yaml}` rule 1 | "Every Done by / Checked by / signature column on this manufacturing operations page is filled." |
| 2 | **Aggregated within a document** | One rule, one page (or one section), evidence drawn from multiple values on that page | `alcoa_rules.{md,yaml}` rule 2 | "On a manufacturing operations page, the row timestamps are monotonic and consistent with the page's start/end time headers." |
| 3 | **Cross-document** | One rule, evidence drawn from a section in document A AND a section in document B, joined on a stable entity key | `reconciliation_rules.{md,yaml}` rule 1 | "For every raw material in the batch_record `material_dispensing` section, there is a matching entry in the raw_material_request `material_request` section (lot match + ±0.5% quantity)." |

## Files in this directory

| File | Status | Notes |
|---|---|---|
| `document_profiles.yaml` | **Active**. New 9-doc-type taxonomy (Akhilesh's 2026-04-28 update). | Single source of truth for document / section type validation. |
| `document_profiles_ref.yaml` | **Snapshot**. Old 6-doc-type profile. | Kept beside the active profile so rule re-authors can cross-reference. Not loaded at runtime. |
| `alcoa_rules.{md,yaml}` | **Active**. Two reference rules — patterns 1 & 2. | Patterns are documented in-line; copy the YAML shape when generating new rules. |
| `reconciliation_rules.{md,yaml}` | **Active**. One reference rule — pattern 3. | Cross-document. Uses `cross_section_requirements` to declare its (document, section) join. |
| `gmp_rules.{md,yaml}` | **Empty**. | The agent runs with zero rules until repopulated. |
| `sop_rules.{md,yaml}` | **Empty**. | As above. |
| `checklist_rules.{md,yaml}` | **Empty**. | As above. |
| `*_rules.archived.{md,yaml}` | **Frozen text reference**. | The 170 historical rules. Not loaded by the registry. Used by the rule-author skill as text content for regeneration. |

## When the rule-author skill runs

1. Read `document_profiles.yaml` to learn the valid document types and
   section types.
2. Read `<agent>_rules.archived.md` for the historical rule TEXT and
   intent (still pharmaceutical-domain-correct; only the type
   references are stale).
3. Read this file and the three exemplars to learn the YAML shape per
   pattern.
4. For each archived rule, classify it into one of the three patterns,
   re-home its `applicable_section_types` / `applicable_document_types`
   / `cross_section_requirements` against the new profile, and emit it
   into the appropriate active `_rules.{md,yaml}` file.
5. Run `validate_compliance_configs(strict=True)` (or boot with
   `AT_COMPLIANCE__VALIDATE_STRICT=1`) to fail-fast on any drift the
   skill introduced.

## Hard rule for the agent

A rule's `applicable_document_types`, `excluded_document_types`,
`applicable_section_types`, and any `in_document_type` /
`section_type` referenced under `cross_section_requirements` MUST
appear in `document_profiles.yaml`. The validator will warn at boot
and fail under `AT_COMPLIANCE__VALIDATE_STRICT=1`. Don't ship rules
that point at types that don't exist.
