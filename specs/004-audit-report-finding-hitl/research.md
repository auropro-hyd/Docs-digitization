# Research: BMR Audit Report & Finding-Level HITL

**Feature**: 004 | **Spec Version**: v2

## R-1. Consolidated grouped view (default) vs. flat-by-severity (toggle)

**Decision**: Default layout groups findings by BPCR step (and by document scope for
non-step-bound findings), with each step exposing collapsible ALCOA / GMP /
Checklist-Adherence sub-sections. A toggle offers flat-by-severity for quick triage.

**Why**:
- Akhilesh call (2026-04-17) confirmed reviewers want the step context as the primary
  mental model — it mirrors how they walk the paper BMR.
- Grouping also makes synthesised checklist findings navigable back to the contributing
  ALCOA/GMP findings (User Story 5 navigation).
- Flat-by-severity is useful for "how bad is this run?" scanning but is secondary.

**Alternatives rejected**: Flat-by-severity default (loses step context); grouped-by-role
(unfamiliar to reviewers).

## R-2. Structured resolution schema vs. free-text

**Decision**: Every DISMISS and CORRECT action MUST produce a `StructuredResolution` with
a required `reason_type` enum and, for value-dependent types, an `observed_value_on_document`
field. `system_extracted_value` is auto-snapshot from the finding and is immutable.
Free-text is an optional `reason_comment`, never a substitute.

**Why**:
- Constitution IX (rule-as-data) requires that reviewer signal is queryable — free text
  cannot seed a feedback corpus reliably.
- Reason types map directly to downstream actions: `OCR_MISREAD` → OCR fine-tuning data,
  `ACCEPTABLE_VARIANCE` → rule-tolerance suggestion, `RULE_MISCONFIGURED` → rule-spec
  tuning proposal (Spec 005 tune mode).
- The enum is YAML-extensible (`config/bmr/resolution-reason-types.yaml`).

**Alternatives rejected**: Free-text with NLP classification post-hoc (unreliable; delays
feedback loop); closed enum only (loses nuance).

## R-3. PDF export engine: WeasyPrint

**Decision**: WeasyPrint (HTML + CSS → PDF) for the consolidated report PDF. ReportLab was
considered.

**Why**:
- BUC §16 layout is long-form, has paged sections, and the team already owns Tailwind
  markup; WeasyPrint lets us reuse a close cousin of the web UI's markup.
- ReportLab gives tighter layout control but requires re-implementing every view in its
  drawing API — high cost for no visible gain.
- WeasyPrint is MIT-compatible and actively maintained.

**Alternatives rejected**: Puppeteer / headless Chrome (heavy runtime dep); LaTeX (overkill
for this form factor).

## R-4. Report sections driven by YAML, not code

**Decision**: `config/bmr/report-sections.yaml` declares the ordered list of sections:

```yaml
sections:
  - id: package_metadata
  - id: executive_summary
    include_overall_score: false    # Constitution — forbidden
  - id: findings_by_severity
  - id: findings_by_alcoa_tag
  - id: rule_evaluation_appendix
  - id: system_confidence_appendix
```

The exporter walks this list and dispatches to a per-section renderer. New sections or
reordering is a YAML edit.

**Why**: Constitution VI + stakeholder-specified layout must be visible in review without
requiring a code change.

**Alternatives rejected**: Hardcoded section order in Python — fails Constitution VI and
makes demo-time layout tweaks painful.

## R-5. Severity gating YAML

**Decision**: `config/bmr/report-severity-gating.yaml` declares which severities block
export when unactioned:

```yaml
gating:
  blocking_severities: [critical, major]
  warning_severities: [minor]          # show a warning but permit export
  info_severities: [info]              # informational only
```

**Why**: The business rule "cannot export while Critical/Major pending" is a policy, not a
constant. Pilot sets {critical, major}; later deployments may tighten or relax.

**Alternatives rejected**: Hardcoded.

## R-6. FeedbackSample seeding and access

**Decision**: Every persisted `StructuredResolution` triggers a synchronous
`feedback_seed.v1` capability call that creates one `FeedbackSample` with:

- `rule_id`, `rule_version`
- `input_context_digest` (stable hash of resolved inputs, from Spec 003's `ResolvedContext`)
- Snapshot of the original finding (including evidence refs — immutable)
- Resolution action + reason_type + observed / system values
- Reviewer identity (for provenance) — hashed at export time for privacy

Spec 005's rule-authoring skill queries these via `/api/v1/feedback/samples?rule_id=…`.

**Why**: Tight coupling to resolution action ensures corpus is authoritative; lazy seeding
would race with skill queries and complicate consistency.

**Alternatives rejected**: Batch seeding (staleness risk); seeding only on CONFIRM (misses
the most valuable signal, which is DISMISS/CORRECT).

## R-7. Correction → selective re-run

**Decision**: A CORRECT action:
1. Writes the new value into the finding's source extraction (extractor port write-through).
2. Creates a `CorrectionWorkflow` row.
3. Invokes Spec 003's reverse-dependency graph to compute the invalidation set.
4. Shows a re-run preview to the reviewer ("These rules will re-evaluate: …").
5. On reviewer confirm, Spec 001's re-run planner runs. Findings may be retracted,
   added, or unchanged; superseded `StructuredResolution` rows are marked `needs_re_action`.
6. Pending `needs_re_action` findings gate export.

**Why**: Reviewer needs confidence that a correction won't silently invalidate hours of
past triage. The preview + explicit confirm matches the "no surprise" principle.

**Alternatives rejected**: Auto-rerun on correction (surprise factor); no-rerun until
export (defeats the purpose of Correct).

## R-8. Evidence viewer — cross-doc navigation

**Decision**: The viewer UI renders a page with highlighted regions. For cross-doc
findings (Spec 003), a source switcher lets the reviewer toggle between source and target
documents; highlights persist across switches. A synthesised finding (from checklist
synthesis) exposes a "Contributing findings" drawer listing the upstream ALCOA/GMP
findings, each with a deep-link to their own evidence view.

**Why**: Addresses the spec's User Story 2 + User Story 5 navigation; aligns with Spec 003
finding detail payload (`matched_document_id` + evidence refs on both sides).

**Alternatives rejected**: Inline-only (loses context on large documents); separate tabs
per source (breaks the "single finding, everything in view" flow).

## R-9. Report revisions

**Decision**: Every export produces an `AuditReportRevision` with a monotonic number and a
pointer to its predecessor. Post-export actions do not mutate the exported PDF or bundle;
they create a new revision on next export. Exports are immutable blobs identified by sha256.

**Why**: Constitution VIII — exports are append-only deliverables. Traceability demands
that the QC Head's signed copy remains exactly retrievable.

## R-10. Session recovery

**Decision**: The frontend persists draft resolutions (form state prior to submit) in
client-side Zustand + localStorage keyed on `(run_id, finding_id, reviewer_id)`. On
reconnect, the reviewer sees their unsent drafts restored. Submitted resolutions are
server-of-truth.

**Why**: Reviewers get interrupted; losing a half-typed reason is a frustrating paper-cut.

**Alternatives rejected**: Server-side drafts (adds an endpoint + consistency problem for
transient state; not worth it).

## R-11. Test strategy

- Backend unit tests per subpackage module.
- Backend integration: `test_happy_path_export.py` (run → action → export).
- Backend contract: exporter bundle JSON validated against a published JSON Schema.
- Frontend component tests per form + viewer component.
- E2E (Playwright): four critical flows (happy path, dismiss-requires-reason,
  correct-triggers-rerun, evidence viewer cross-doc).
- Performance: export benchmark at 200 findings (≤ 15 s p95).
