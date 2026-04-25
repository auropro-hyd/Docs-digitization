# Feature Specification: Rule-Spec Schema & Authoring

**Feature Branch**: `005-rule-spec-and-authoring`
**Created**: 2026-04-17
**Status**: Draft
**Input**: User description: "Publish a versioned, machine-validatable rule-spec schema (YAML + JSON Schema) including the new `context_object` field, plus a Cursor skill (`bmr-rule-author`) that turns a natural-language rule description + pilot context into a validated YAML rule ready to drop into `backend/config/rules/`. This is the declarative surface that makes Constitution IX real and closes the feedback-corpus loop from Spec 004."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Internal rule author drafts a new cross-document rule from a natural-language description (Priority: P1)

A pharma SME says: "In step 3, the weight recorded on the BPCR must equal the weight on the corresponding raw-material dispensing page within 0.1 kg; if it doesn't, it's an ALCOA-Accurate Major finding." The author opens Cursor, invokes the `bmr-rule-author` skill with this description and a pointer to the pilot rule bank, and receives a validated YAML rule entry. They drop the file into `backend/config/rules/pilot/alcoa/`, the pipeline picks it up on next run, and the new check fires on the pilot fixture.

**Why this priority**: This is the whole point of Constitution IX (rule-as-data). Without a working authoring path, the "framework not custom code" pitch falls apart.

**Independent Test**: Run the skill on a natural-language description + pilot context; verify the skill produces a YAML rule that (a) validates against the schema, (b) references real roles from the pilot manifest, (c) declares a valid `context_object`, and (d) actually fires against a fixture planted with a known mismatch.

**Acceptance Scenarios**:

1. **Given** the author provides a clear NL rule + manifest reference, **When** the skill runs, **Then** it emits a YAML rule with `rule_id`, `context_object`, `evaluation`, `alcoa_principle`, `severity`, and `fallback` fully populated.
2. **Given** the generated rule is loaded into the pipeline, **When** the pipeline starts, **Then** the rule validates against the published JSON Schema and loads without error.
3. **Given** the rule runs on the pilot fixture, **When** a planted mismatch exists, **Then** the rule emits a finding with correct evidence; when no mismatch, no finding.

---

### User Story 2 — Rule-spec schema is published and versioned (Priority: P1)

Every rule YAML file declares `schema_version`. The JSON Schema for rules lives under `backend/config/rules/schema/rule.schema.json` (or equivalent versioned path). Changes to the schema produce a new schema_version; rules pinned to an older version continue to validate against that version.

**Why this priority**: Versioning is what makes prior runs reproducible and makes schema evolution safe.

**Independent Test**: Load the pilot rule bank with mixed schema versions; assert each rule validates against its declared version; assert a rule with an unknown schema_version is rejected at load time.

**Acceptance Scenarios**:

1. **Given** a rule YAML with `schema_version: "1.0"`, **When** the loader validates it, **Then** the `1.0` schema is used.
2. **Given** the schema is amended (new field added) and bumped to `1.1`, **When** a rule declares `schema_version: "1.0"`, **Then** the `1.0` validator still accepts it (backward compatibility).
3. **Given** a rule declares an unknown schema_version, **When** the loader runs, **Then** the pipeline refuses to start with a clear error.

---

### User Story 3 — Authoring skill validates a draft rule against fixtures before acceptance (Priority: P1)

The skill does not just produce YAML; it also runs the draft rule against the pilot fixture (or a user-supplied fixture) and reports: (a) does the rule load, (b) does it fire when it should, (c) does it produce the expected evidence, (d) does it produce false positives on a negative fixture. The author sees a concrete pass/fail report before committing the YAML.

**Why this priority**: Without fixture validation, authors produce rules that "look right" but misfire at runtime. The skill closing the loop is what makes rule authoring safe.

**Independent Test**: Run the skill with a draft rule, a positive fixture (should fire), and a negative fixture (should not fire). Verify the skill reports both outcomes and refuses to mark the rule "ready" unless both pass.

**Acceptance Scenarios**:

1. **Given** a draft rule + positive fixture, **When** the skill runs, **Then** it reports "fires, evidence: [...]".
2. **Given** a draft rule + negative fixture, **When** the skill runs, **Then** it reports "does not fire".
3. **Given** either check fails, **When** the skill reports, **Then** it prints the specific failure (schema error / wrong evidence / false positive) and marks the rule `NOT_READY`.

---

### User Story 4 — Skill proposes rule tunings from the feedback corpus (Priority: P2)

After a run, the feedback corpus (Spec 004) contains resolutions showing that rule `R1` is firing spuriously on OCR misreads of a specific field. The author invokes the skill in "tune" mode; it analyses FeedbackSamples for rule_id `R1`, proposes a tighter `tolerance` or a new alias entry, and shows the author a diff of the YAML before committing.

**Why this priority**: This is the long-term value of the feedback corpus. P2 because it is polish for iteration 2+; the pilot's first month runs without it.

**Independent Test**: Seed the feedback corpus with 10 samples showing a systematic OCR misread pattern. Invoke the skill in tune mode on rule R1. Verify the skill emits a sensible tuning proposal (either a tolerance widen or an alias entry) with the FeedbackSample ids it used as evidence.

**Acceptance Scenarios**:

1. **Given** the corpus shows a clustered `OCR_MISREAD` pattern for rule R1, **When** the skill runs in tune mode, **Then** it proposes either a tolerance adjustment or an alias addition with cited evidence.
2. **Given** the author accepts the tune, **When** they save, **Then** the skill writes a new rule version (semver bump) rather than editing in place.

---

### User Story 5 — Author migrates a legacy Python-coded SOP check into a rule (Priority: P2)

The existing SOP agent contains hardcoded Python for "signatures required on every weighing step". The author invokes the skill with the Python source and asks "convert this to a rule". The skill produces a draft YAML rule and runs the same fixtures the Python path was tested against; both produce identical findings.

**Why this priority**: This is the SOP-retirement migration path. P2 because the SOP agent is already decoupled at runtime; converting legacy logic happens in the background.

**Independent Test**: Given a known SOP check, convert it via the skill. Verify the resulting YAML produces the same findings as the Python path on the same fixture.

**Acceptance Scenarios**:

1. **Given** a Python SOP check, **When** the skill runs in migrate mode, **Then** it produces a YAML rule and a diff report confirming outputs match on the fixture.
2. **Given** the YAML rule is installed, **When** the pipeline runs, **Then** the legacy Python path can be unregistered (and SOP agent runtime can be fully removed) without regression.

---

### Edge Cases

- NL description is ambiguous (e.g., "should be about right"): skill asks the author to clarify; does not guess a tolerance.
- Rule references a role the manifest does not declare: skill refuses to produce the YAML and lists valid roles.
- Skill cannot resolve a capability for the described evaluation (no matching capability in the registry): skill flags "needs new capability" and produces a stub rule + an issue template pointing at the plan.md process.
- NL description spans two rules: skill splits into two YAML entries with distinct rule_ids and asks the author to confirm.
- Schema has a breaking change pending: skill warns and refuses to produce a rule pinned to the unreleased version; author must pin to the latest released version.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST publish a versioned JSON Schema for rule specifications under `backend/config/rules/schema/rule.schema.vX.Y.json`. Every rule YAML MUST declare a `schema_version` field.
- **FR-002**: The schema MUST define all fields required by the runtime: `rule_id`, `schema_version`, `rule_version`, `applicable_pages`, `context_object`, `evaluation`, `alcoa_principle`, optional `gmp_category`, `severity`, `fallback`, optional `synthesises_from`, `tolerance` (when applicable), optional `multiplicity`, optional `description`, optional `authored_by`, optional `feedback_origin` (pointing at feedback samples that motivated the rule).
- **FR-003**: The schema MUST validate `context_object` shape exhaustively: discriminated on `scope ∈ { same_page, page_aggregate, cross_document }`, with per-scope required fields (see Spec 003 §FR-009).
- **FR-004**: The rule loader MUST validate every rule against its declared schema version at pipeline startup; validation failures MUST abort startup with a pointer to the offending rule + field.
- **FR-005**: The rule loader MUST compute and record a content hash per rule (`rule_version`) at load time and attach it to every finding produced by that rule. This allows prior runs to be reproduced deterministically.
- **FR-006**: A `bmr-rule-author` Cursor skill MUST exist at `.cursor/skills-cursor/bmr-rule-author/SKILL.md` (workspace-level) that accepts: (a) a natural-language rule description, (b) an optional pointer to the pilot manifest + rule bank for context, (c) optional positive/negative fixtures, (d) mode: `author | tune | migrate`.
- **FR-007**: The skill MUST emit a draft YAML rule, validate it against the published schema, load it into an ephemeral pipeline instance, run it against the supplied fixtures, and report findings + schema errors + evidence correctness. A rule is marked `READY` only when schema-valid AND all fixture assertions pass.
- **FR-008**: In `tune` mode, the skill MUST query the feedback corpus (Spec 004 FR-018) for samples bearing a given `rule_id`, cluster them by `reason_type`, and propose a concrete YAML diff (tolerance adjustment, alias addition, severity change, or `synthesises_from` adjustment) with citations.
- **FR-009**: In `migrate` mode, the skill MUST accept a reference to a Python compliance function (typically under `app/compliance/sop.py`) and produce a draft YAML rule; it MUST run side-by-side on the same fixture and assert identical outputs before marking the rule `READY_TO_REPLACE`.
- **FR-010**: The skill MUST NOT write to `backend/config/rules/` directly; it proposes a file and the author commits it. This keeps the author in the loop (Constitution I — human auditor mirror).
- **FR-011**: The skill's output MUST be deterministic for the same inputs (within LLM non-determinism bounds). When non-deterministic, it MUST report an input hash so re-invocations are reproducible for audit.
- **FR-012**: Documentation of the rule-spec schema MUST be auto-generated from the JSON Schema into `backend/config/rules/schema/rule.schema.vX.Y.md` (human-readable). Rule authors (including non-programmers) read the markdown, not the JSON.
- **FR-013**: The schema MUST support deprecation: a rule may declare `deprecated: true` with `superseded_by: <rule_id@version>`. Deprecated rules are loaded but emit only a log entry and DO NOT run, preserving finding reproducibility for old runs without polluting new ones.
- **FR-014**: Rule-spec validation errors MUST be surfaced to the author with file path, line number, and a human-readable description of what the schema expected. No opaque JSON Schema errors reach the author.

### Key Entities *(include if feature involves data)*

- **RuleSpec**: The YAML document declaring one rule. Fields per FR-002.
- **RuleSchema**: The JSON Schema that validates a RuleSpec. Versioned.
- **RuleBank**: A directory of RuleSpec files (e.g., `backend/config/rules/pilot/alcoa/`). Loaded atomically at pipeline startup.
- **AliasTable**: (shared with Spec 003) — declarative YAML mapping canonical → aliases per domain.
- **AuthoringSkill (bmr-rule-author)**: A Cursor skill file at `.cursor/skills-cursor/bmr-rule-author/SKILL.md` declaring its modes, inputs, outputs, and validation steps.
- **ValidationReport**: The skill's output per authored rule. Carries schema validity, fixture-run outcomes, evidence-correctness verdict, and READY/NOT_READY flag.
- **TuneProposal**: The skill's output in tune mode. Carries rule_id, cited FeedbackSample ids, a YAML diff, and rationale.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On the pilot rule bank (about 30–50 rules), 100% of rules validate against the published schema. Rule loading fails fast for any invalid rule.
- **SC-002**: Authoring a new cross-document rule from an NL description to a READY YAML takes under 10 minutes p95, measured across 5 SME trials.
- **SC-003**: Every rule in the pilot bank carries a content-hash `rule_version`, and findings emitted by the pilot run reference the exact rule_version present at load time. Verifiable by a fixture test that asserts finding.rule_version matches the loaded bank.
- **SC-004**: On a seeded feedback corpus of 10 `OCR_MISREAD` samples for one rule, `tune` mode produces a concrete YAML diff that, when applied, eliminates the false positives on a replay of the same fixture.
- **SC-005**: On a seeded legacy SOP Python function, `migrate` mode produces a YAML rule that matches the Python output on a 20-fixture suite within one iteration 95% of the time; the remaining 5% surface clear reasons (missing capability, ambiguous NL in the source comment).
- **SC-006**: The `bmr-rule-author` skill is discoverable in Cursor (appears in the skills list) and includes a SKILL.md that lists its modes, inputs, and invocation examples.
- **SC-007**: Deprecated rules do not fire at runtime but remain reproducible for prior runs (a prior run can be replayed and emit the same findings it originally did).

## Assumptions

1. **Existing rule engine is extensible**. `backend/app/compliance/evaluator.py` and `context_builder.py` can accommodate a new `context_object` field without rewriting the loader; spec 003 owns the runtime plumbing and spec 005 owns the schema + authoring skill.
2. **Rule authors have Cursor access**. The `bmr-rule-author` skill is a Cursor skill, not a standalone CLI, because the primary authoring flow is in-IDE.
3. **Pilot rule set fits within v1 schema**. If an SOP check discovered during migration cannot be expressed, spec 001 / spec 003 accepts new-capability additions; spec 005's schema can grow without retiring v1.0.
4. **Feedback corpus (Spec 004) is live** by the time tune mode is used in anger. Tune mode without corpus returns "insufficient data" and no-op.
5. **YAML is the authoring format**. JSON remains the schema format (for tooling). Other formats (e.g., TOML) are out of scope.
6. **Internal authors only for v1**. End-users (pharma QC staff) may request rules but will not author them directly; that's a later UX effort.
