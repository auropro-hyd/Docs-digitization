<!--
Sync Impact Report
==================
Version change: 1.0.0 → 1.1.0
Bump rationale: MINOR amendment reflecting the directional shift captured in the "Call with
Akhilesh" (2026-04-17). The programme now explicitly prefers leveraging the existing
compliance framework over reconstructing a human-auditor process-replication from scratch.
Process-replication remains available as a degraded-mode fallback (see Principle I).
Principle II is flattened from 7 ordered stages to 5 stages with internal parallelism and
softer gates. Principle VII is strengthened from "don't regress old modes" to "the existing
framework IS the backbone; additions are orchestration + rule-engine extensions". A new
Principle IX ("Rule-as-Data, Not Rule-as-Code") is introduced to formalise the declarative
rule-spec extension (`context_object`). No principle is removed; no principle is renamed;
existing principle numbering is preserved.

Amended principles (4):
  I.   Human Auditor Mirror, Not Replacement — softened: "leverage-first, replicate-as-fallback"
  II.  Staged Pipeline With Hard Gates — flattened to 5 stages, internal parallelism allowed
  IV.  Single Final Checkpoint & Selective Re-Execution — legibility-HITL scope narrowed
  VII. Backward Compatibility With Existing Pipeline — strengthened to "existing framework
       is the backbone"

Added principles (1):
  IX.  Rule-as-Data, Not Rule-as-Code

Unchanged principles (5):
  III, V, VI, VIII, and the Architectural Constraints / Development Workflow / Governance
  sections (minor edits only to reflect the new stage shape and rule-as-data surface).

Templates requiring updates:
  ✅ .specify/templates/plan-template.md — Constitution Check gate will be re-derived to
     include a Principle IX gate and a softer Principle II gate.
  ✅ .specify/templates/spec-template.md — No change required.
  ✅ .specify/templates/tasks-template.md — No change required.

Specs requiring updates:
  ✅ specs/001-bmr-audit-pipeline  — rewrite for 5-stage flatter pipeline with parallel compliance
  ✅ specs/002-document-package-classification — add boundary method hierarchy + configurable summary
  ✅ specs/003-cross-document-reconciliation  — trim to "Cross-Document Rule Support"
  ✅ specs/004-audit-report-finding-hitl      — add structured-resolution comment + feedback sample
  ✅ specs/005-rule-spec-and-authoring (NEW)  — declare rule-spec schema + authoring skill

Follow-up TODOs: none.
-->

# Docs-Digitization / BMR Audit Constitution

## Core Principles

### I. Human Auditor Mirror, Not Replacement — **Leverage-First**

The system exists to accelerate and de-risk a human auditor's workflow, not to replace human
judgment. The **primary architectural posture is to leverage the existing compliance framework**
(ALCOA agent, GMP agent, rule-engine, OCR, VLM, review store) and extend it with additional
capabilities, rather than rebuild a pipeline that mimics a human auditor step-by-step.

Process-replication (sequential stage-walk mirroring the human workflow) is retained **only as
a degraded-mode fallback** when the leverage approach produces findings of unacceptable quality
(e.g., unmanageable volume of false positives, untrainable systematic errors). Switching to
degraded mode is an explicit, recorded decision — never an implicit default.

Every capability — whether in the leveraged path or the fallback path — MUST either produce
evidence the auditor can independently verify (document + page + region + rule), OR request a
human decision when confidence is below the configured threshold.

**Rules**:
- No compliance verdict is final without an explicit human action (Confirm, Dismiss, or
  Correct) at the designated HITL checkpoint. A silent auto-approve path to the final report
  is a violation.
- Choice of leverage vs. degraded-mode MUST be recorded in the run's audit trail with the
  triggering condition (e.g., "findings_count > threshold").
- Degraded mode MUST NOT be the default for a pilot or production run unless explicitly
  opted-in by operator intent.

**Rationale**: Pharmaceutical audits are legally binding artifacts; the system's role is
assistive only. Auditors remain accountable. Rebuilding a process-replication pipeline from
scratch would discard a working, tested framework and expand schedule risk. The objective is
audit-defensible findings, not architectural purity about how the findings were produced.

### II. Staged Pipeline With Soft Gates and Parallel Compliance

BMR audits execute as a short ordered pipeline of **5 stages**:

`Ingest → Legibility & Classification → Structured Extraction & Summarisation → Compliance (ALCOA ∥ GMP ∥ Checklist-Synthesis, parallel) → Report & Resolution`

Within a stage, work over independent scopes (documents, pages, BPCR steps) MAY run in
parallel. Between stages, a scope MUST clear its predecessor's gate before the downstream
stage processes it.

**Rules**:
- No compliance check runs on a page that has failed the Legibility gate for that page. The
  page is flagged for HITL (re-upload or proceed) while the rest of the pipeline continues
  for other pages.
- The Compliance stage runs ALCOA and GMP agents **in parallel** by default. The
  Checklist-Synthesis capability runs **after** ALCOA and GMP outputs are available within
  that stage, because it is derivative (see Principle I — it synthesises from existing
  findings where possible).
- Gate failures are recorded as findings and route the affected **scope** into HITL, not the
  whole package.
- SOP is not a separate stage or capability: SOP-derived rules are extracted upfront (offline,
  during rule authoring — see Principle IX and Spec 005) and categorised into ALCOA or GMP
  rule banks. At runtime there is no "SOP agent".

**Rationale**: Running compliance checks on unreliable inputs produces unreliable verdicts.
But overly rigid sequential staging destroys the existing framework's natural parallelism
between ALCOA and GMP, and inflates latency. The 5-stage shape keeps gates meaningful while
letting the compliance layer exploit existing parallelism.

### III. Capability-First, Not Agent-First

Code MUST be organized around **atomic capabilities** (legibility-check, boundary-detect,
page-summary, doc-summary, signature-detect, quantity-reconcile, timestamp-sequence,
step-rule-eval, cross-doc-rule-eval, checklist-synthesise). Each capability is:

- Single-responsibility (one verifiable claim, or one well-defined derivation).
- Independently callable (so selective re-run works without running the whole pipeline).
- Composable into agents and stages via orchestration, not inheritance.

ALCOA+, GMP, and Checklist classifications are **tags on findings**, not class hierarchies.
The ALCOA and GMP "agents" are **thin orchestrators over capabilities and rule banks**, not
monolithic bodies of logic.

**Rules**:
- A new compliance behaviour MUST first be attempted as a rule-spec entry (Principle IX). A
  new capability is introduced only when the behaviour cannot be expressed as a rule against
  existing capabilities.
- No behavioural branch belongs inside a monolithic agent; it belongs in a capability or a
  rule.

**Rationale**: Principle-partitioned monoliths duplicate logic and prevent selective re-run.
Capability-partitioned code is reusable across clients and stages; rule-partitioned
configuration is reusable across capabilities.

### IV. Single Final Checkpoint & Selective Re-Execution

Only **one** HITL checkpoint gates the final audit report. The legibility gate has its own
lightweight HITL, narrowly scoped: the reviewer may only (a) re-upload a page, or
(b) proceed-anyway. No other mid-pipeline HITL exists.

**Rules**:
- When a reviewer corrects a finding at the final checkpoint, the system MUST re-execute only
  the capabilities that depend on the corrected input, bounded by document, page, or BPCR step.
- Full-pipeline re-runs on a single finding correction are a violation.
- The re-run scope MUST be reported to the reviewer ("3 findings will be re-evaluated") before
  re-execution begins.
- Legibility HITL is narrow by design — it MUST NOT offer finding-level actions; those belong
  only at the final checkpoint.

**Rationale**: Multiple checkpoints fragment the reviewer's attention and multiply latency.
Selective re-run contains the cost of corrections. A narrow legibility HITL preserves the
one-checkpoint principle while still allowing page-level recovery without running the full
pipeline on unreadable input.

### V. Evidence-Bound Findings (NON-NEGOTIABLE)

Every finding produced by the system MUST carry:

- `document_id`, `page_number`, and (where possible) `region` (bounding box or text span)
- `capability_id` that produced it and `rule_id` it evaluates
- An ALCOA+ principle tag and severity (Critical / Major / Minor / Observation)
- Raw extracted value and expected value, so a reviewer can verify without re-running the
  pipeline
- For synthesised findings (Checklist-Synthesis): the **source findings** from ALCOA / GMP
  that the synthesis depends on (so retracting a source finding retracts its synthesised
  descendants)

A finding without resolvable evidence is a bug and MUST NOT reach the report.

**Rationale**: "The system says so" is not audit-defensible. Every verdict needs traceable
proof, including for derivative findings whose proof is another finding.

### VI. Configurable Framework, Not Hardcoded Workflow

The pipeline MUST be configurable per client / product / BPCR template via declarative config
(manifests, rule bindings, reconciliation tolerances, summary templates). Hardcoding a
specific client's document layout, step numbering, or rule thresholds into Python code is a
violation.

**Rule**: New clients onboard by writing manifests and rules, not by patching orchestration
code. The pilot client's BMR layout is one valid manifest instance, not the schema.

**Rationale**: The product pitch is a framework that adapts to any manufacturer's SOPs.
Coupling orchestration to one layout destroys that pitch.

### VII. Backward Compatibility — **Existing Framework Is The Backbone**

The existing compliance pipeline (ALCOA agent, GMP agent, compliance graph, rule engine, OCR
engines, VLM providers, review store, HITL adapters) IS the backbone of the BMR audit
capability. New work is:

1. **Additive orchestration** (a new LangGraph composition for the BMR flow), and
2. **New capabilities** for gaps the existing framework does not cover (legibility
   pre-check, boundary detection, configurable summary, page-level aggregation, cross-document
   rule evaluation, checklist synthesis), and
3. **Rule-engine extensions** (Principle IX: `context_object`).

Rewriting the compliance engine from scratch or replacing the existing agents with an
entirely new abstraction is a violation unless the leverage approach has been tried, measured,
and demonstrably failed against the objective (see Principle I — degraded-mode fallback).

**Rules**:
- The existing `accuracy | quality | reasoning | byok | production` single-document pipeline
  modes MUST continue to work unchanged.
- Shared code touched by BMR features MUST carry regression tests for the older modes.
- Old ALCOA / GMP / SOP / checklist agent entrypoints remain callable; they evolve by gaining
  new capabilities, not by being deleted. The SOP agent is the one exception — see Principle
  II — and even there, the SOP file remains as a rule-extractor utility consumed offline.

**Rationale**: The existing framework is tested, integrated with the UI, and carries the
pilot client's rules. Throwing it away to build a 7-stage process-replication from scratch
is schedule suicide for a 3–4 week timeline and expands regression risk. Leverage wins.

### VIII. Pharma Data-Integrity Floor (ALCOA+)

Every finding, correction, and HITL action recorded by the system MUST be:

- **Attributable** — stored with user identity and timestamp
- **Legible** — structured data, not free-text blobs
- **Contemporaneous** — persisted at the time of action, not reconstructed later
- **Original** — raw extracted values are immutable; corrections produce a new revision, not
  an overwrite
- **Accurate** — all numeric comparisons declare tolerance explicitly; no implicit equality
- **Complete / Consistent / Enduring / Available** — audit trail retained per data-retention
  policy

Reviewer resolutions at the final checkpoint MUST capture a **structured comment**: at minimum
a `reason_type` (e.g., `OCR_MISREAD`, `ACCEPTABLE_VARIANCE`, `OUT_OF_SCOPE`, `OTHER`), the
observed value on the document, the system-extracted value, and an optional free-text note.
Free-text-only resolutions are a violation (they cannot be aggregated as training signal; see
Principle IX).

The internal audit trail IS itself an audit artifact and MUST withstand GMP inspection.

**Rationale**: Regulators may subpoena how a verdict was reached. Structured resolutions also
produce the training-signal corpus that Principle IX depends on.

### IX. Rule-as-Data, Not Rule-as-Code

Compliance behaviour is expressed primarily as **rule specifications** — declarative YAML
files with a published, versioned schema — not as Python conditionals. The rule spec MUST
support at minimum:

- `rule_id` (stable, client/product-scoped)
- `applicable_pages` (existing)
- `context_object` (new): declares what additional context the rule needs — same page,
  aggregated across pages in the same document, or cross-document with a named role
  (e.g., `role: RawMaterialPage`), plus the entity-matching strategy (e.g., by step number,
  by material name)
- `evaluation`: the capability invoked, its inputs, its tolerances (for numeric comparisons)
- `alcoa_principle`, `gmp_category`, `severity`
- `fallback`: what to do when the context is missing or the capability cannot evaluate
  (`flag_as_unevaluated`, `flag_as_indeterminate`, `treat_as_pass`, etc.)
- Optional `synthesises_from`: list of `rule_id`s whose findings allow this rule to be
  satisfied by synthesis rather than direct evaluation (Checklist-Synthesis pattern)

**Rules**:
- A new compliance behaviour MUST first be attempted as a new rule entry. A new capability is
  introduced only when the behaviour cannot be expressed in the rule schema.
- Rule YAML files MUST be versioned; changes to a rule produce a new `rule_id` version, not
  an in-place edit, so prior runs remain reproducible.
- Rule authoring MUST be supported by a published skill/tool (see Spec 005) that validates a
  rule against the schema and against a corpus of test cases before it is accepted.
- Structured reviewer resolutions (Principle VIII) MUST be accumulable as a training-signal
  corpus for rule-spec tuning and OCR fine-tuning.

**Rationale**: The practice's economics depend on onboarding new clients by writing rules, not
by writing code. A machine-readable rule spec with explicit context semantics is the only way
to keep client-specific behaviour out of the engine. It is also the only way to close the
loop from reviewer feedback to rule improvement.

## Architectural Constraints

- **Hexagonal ports preserved**: capabilities depend on ports (`OCREngine`, `LLMClient`,
  `VLMClient`, `ReviewStore`, `DocumentStore`), never on concrete adapters.
- **LangGraph is the only orchestration runtime**: no ad-hoc coroutine chains outside a
  declared graph.
- **Postgres checkpointer is authoritative**: pipeline state persists across restarts;
  selective re-run reads from checkpoint.
- **No overall compliance score**: the report presents findings by severity and ALCOA tag. A
  single rolled-up "compliance %" is explicitly rejected by the QC Head stakeholder.
- **Filesystem JSON remains the document-of-record**: Postgres stores orchestration state,
  not the audit artifact.
- **Rule spec files are first-class**: they live under `backend/config/rules/` (or a
  client-namespaced subpath), are validated at load time against a published JSON Schema,
  and their hashes are recorded into each run's audit trail for reproducibility.

## Development Workflow

- **Spec-first**: new BMR capabilities require a spec in `specs/NNN-name/` with user stories
  (prioritized P1/P2/P3), functional requirements, key entities, and measurable success
  criteria, BEFORE implementation.
- **Rule-first within a spec**: when a feature adds compliance behaviour, the spec MUST show
  the rule-spec entry(ies) it introduces before the capability code it relies on. If a new
  capability is required, the spec MUST explain why the rule spec was insufficient.
- **Capability contracts before orchestration**: define capability input/output contracts
  before wiring them into the graph.
- **Integration tests at package granularity**: BMR tests run against a realistic
  multi-document package (BPCR + raw-material pages + checklists + analysis report), not a
  single PDF.
- **SME walkthrough required**: every P1 user story MUST be walked through by a QA or pharma
  SME on a real BMR package before marking done.

## Governance

This constitution supersedes ad-hoc design decisions. Amendments require:

1. A pull request modifying this file with a justification section (Sync Impact Report as
   HTML comment).
2. Sign-off from the technical lead AND the QA / pharma domain SME.
3. Migration notes for any existing specs affected by the amendment.
4. Propagation to `plan-template.md` Constitution Check gate if principles change or are
   added.

Complexity MUST be justified against these principles in the `Complexity Tracking` section of
each plan. When in doubt, prefer the path that produces more defensible, more auditable
evidence, even at the cost of latency.

**Version**: 1.1.0 | **Ratified**: 2026-04-17 | **Last Amended**: 2026-04-17
