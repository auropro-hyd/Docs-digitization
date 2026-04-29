# Feature Specification: BPCR Layout-Aware Section Detection

**Feature Branch**: `feat/007-bpcr-layout-aware-sections`
**Created**: 2026-04-29
**Status**: Draft (heuristic-only spike implemented; canonical section list and VLM mode pending the call)
**Input**: Client narrowing of point 4 in the 2026-04-28 reply: *"once all the document boundaries are extracted with current setup, we will run layout-aware section detection only for BPCR (1–35 pages) only. rest can be left as is."*

---

## Background (non-normative)

Point 4 in the 2026-04-28 client reply was the only item left as a "topic for the call" rather than a code fix. The client's follow-up narrows the scope and unblocks an implementation that would have otherwise required a much larger spec:

- **Document boundary detection stays as today.** `app/compliance/segmentation.py` (LLM-driven) continues to produce a `DocumentSegmentation` for the whole package.
- **Then, for the document the classifier already tagged as `BPCR` (typically 1–35 pages), we run a second pass** — layout-aware section detection — to identify sub-sections within that BPCR (e.g. *Material Dispensing*, *Granulation*, *Yield Calculation*, *In-Process QC*).
- Every other document (`BMR`, `MFR`, raw-material releases, label specs, …) is left untouched.

The narrowing matters because:

1. **Existing plumbing covers it.** `document_role: BPCR` already flows end-to-end (rules, fixtures, validator, ingest classifier). The selector for *"only the BPCR document"* is trivial — no global rewrite.
2. **Bounded blast radius.** Section detection runs across at most ~35 pages per package, not the whole multi-document upload. Worst-case cost stays an order of magnitude below an unscoped pass.
3. **Additive to the rule contract.** Rules that don't reference sections behave identically; the schema bump (1.0 → 1.1) is back-compatible.
4. **Lets us A/B cleanly.** "BPCR-with-sections" vs. "BPCR-without-sections" is a one-flag comparison, so we can prove the lift before turning it on by default.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Rule author targets a single BPCR sub-section without listing page numbers (Priority: P1)

A rule author wants to write *"sum the dispensed weights in the Material Dispensing section of the BPCR and compare to the MFR target"*. Today they have to either know the page range and bake `page_index in: [4, 5, 6]` into the rule (brittle — page numbers shift across batches), or they have to write the rule against the whole BPCR and rely on field naming to filter (lossy). With section detection they declare `section_id: material_dispensing` in the rule's `page_selector`; the engine resolves it to the correct page range at evaluation time, per package.

**Why this priority**: This is the change's reason-to-exist. Without section-targeting, every aggregate rule against a BPCR section is page-index-fragile and can silently include or exclude data when the BPCR layout shifts.

**Independent Test**: Author a `page_aggregate` rule with `page_selector: { document_role: BPCR, section_id: material_dispensing }`. Run it against the pilot fixture (BPCR with 35 pages) using `bmr-rules fixture-run`. The rule must evaluate against exactly the pages the section detector tagged as `material_dispensing`, and the rule must continue to pass when the same pages are renumbered (e.g. a leading cover page is added).

**Acceptance Scenarios**:

1. **Given** a BPCR with detectable section headers, **When** the section detector runs, **Then** every page in the BPCR is assigned exactly one `section_id` from the canonical section list (or `unsectioned` if no header was confidently detected).
2. **Given** a rule's `page_selector.section_id == "yield_calculation"`, **When** the rule evaluates, **Then** the rule receives only pages whose detected section is `yield_calculation`.
3. **Given** a rule with no `section_id` (a v1.0-shape rule), **When** the rule evaluates, **Then** behaviour is byte-identical to today — no section filter is applied.
4. **Given** the same BPCR is uploaded with one extra cover page prepended, **When** a section-targeted rule re-evaluates, **Then** the rule still selects the same logical pages (now at higher page indices).

---

### User Story 2 — Reviewer sees which sections produced which findings (Priority: P2)

When a reviewer opens an audit report and a finding fires, they want to know which BPCR section the finding belongs to (e.g. *"Yield Calculation"* vs. *"Material Dispensing"*) without cross-referencing the page number against the BPCR table of contents. The finding's evidence already carries `(doc_id, page_index)`; we extend it with `section_id` whenever the page belongs to a detected section.

**Why this priority**: Findings are the user-facing output. Surfacing the section is a small backend change with a large UX win — reviewers triage by section, not by page.

**Independent Test**: Run the pilot end-to-end with a BPCR that has a known mismatch in *Yield Calculation*. The persisted `FindingRecord.evidence[].section_id` must equal `"yield_calculation"` for every evidence entry pointing at a Yield Calculation page.

**Acceptance Scenarios**:

1. **Given** a BPCR with section detection enabled, **When** a finding cites a page in the BPCR, **Then** the finding's evidence carries the corresponding `section_id` (or `null` if the page wasn't sectioned).
2. **Given** a finding cites a page in a non-BPCR document, **When** the finding is persisted, **Then** `section_id` is omitted (no schema noise on documents we don't section).

---

### User Story 3 — Operator turns the new pass off without redeploying (Priority: P2)

An operator running a fleet of BMR jobs wants the freedom to turn section detection off mid-incident (e.g. a regression in the heuristic mis-tags pages and is producing bad findings). One env var disables the post-extract enrichment; existing rules without `section_id` keep working unchanged. Rules that *do* use `section_id` either degrade to "all BPCR pages" or are explicitly marked unevaluated, depending on configuration.

**Why this priority**: Anything new and on-by-default needs a rollback switch. Without one, the only mitigation is a redeploy.

**Acceptance Scenarios**:

1. **Given** `AT_BMR__BPCR_SECTIONS_ENABLED=false` is set, **When** the BMR run executes, **Then** no section detection runs, no `section_id` is written to any extracted page, and rules that reference `section_id` either fall back to "all BPCR pages" or emit `UNEVALUATED` per the configured policy.
2. **Given** the section detector raises an exception on a malformed OCR layout, **When** the run continues, **Then** the run completes with `section_id` unset for the affected document and a `bpcr.section_detect.failed` warning in the log; **no rule fires erroneously** because of partial section data.

---

## Functional Requirements *(mandatory)*

### Detection

- **FR-001**: The system SHALL provide a `detect_bpcr_sections(...)` capability that takes the OCR layout for a single BPCR document and returns a list of `SectionSpan(name, section_id, start_page, end_page, confidence, detection_method)` covering every page in the document. Pages that cannot be confidently sectioned MUST be returned with `section_id="unsectioned"`.
- **FR-002**: Section detection SHALL only run on documents whose classifier-assigned `role == "BPCR"`. Other documents MUST NOT be touched.
- **FR-003**: The capability SHALL accept a `mode` parameter — `heuristic` | `vlm` | `hybrid` — with `hybrid` as the default once the VLM path lands. The v0 spike SHIPS `heuristic` only and exposes `vlm`/`hybrid` as `NotImplementedError` until the canonical section list is locked.
- **FR-004**: The heuristic mode MUST detect section headers that appear **at the top of a page**, **at the top of a table**, OR **mid-page** (per the 2026-04-28 client reply, the *Yield Calculation* header sits in the second half of its page). The heuristic uses (a) regex match against the canonical section list, (b) OCR word position (top-of-page band, top-of-table-row band, mid-page band), and (c) word style (bold / large font where available).
- **FR-005**: The capability SHALL be deterministic for a given OCR layout + canonical-section-list + heuristic configuration. Re-running the detector on the same inputs MUST produce byte-identical output.
- **FR-006**: The capability SHALL fail open: any unhandled exception during detection MUST result in the BPCR being returned as a single `unsectioned` span covering all pages, plus a structured warning log line `bpcr.section_detect.failed`.

### Canonical Section List

- **FR-007**: The canonical list of BPCR sections SHALL live in `backend/config/bmr/pilot/bpcr-section-spec.yaml` as data — not in code. The file declares each section's `section_id`, `display_name`, regex aliases, and detection bands. The capability loads it at construction time; redeploys are required to change it.
- **FR-008**: The canonical list SHALL be versioned (`spec_version: "1.0"`). When the list changes meaning (renamed `section_id`, removed entry), the version MUST bump and a CHANGELOG entry MUST land in the same commit.
- **FR-009**: A new `unsectioned` sentinel `section_id` SHALL be reserved and SHALL NOT be authorable by the spec file (the loader must reject it). It is emitted only by the detector when no canonical match wins.

### Plumbing into the BMR pipeline

- **FR-010**: After Stage 3 extraction completes, the system SHALL run a post-extraction enrichment that calls `detect_bpcr_sections` for each BPCR document in the `ExtractedPackage`. The enrichment MUST attach the resulting `section_id` to each `ExtractedPage` for the BPCR.
- **FR-011**: The enrichment MUST NOT re-write any `FieldValue`, MUST NOT change the `ExtractedPackage.package_id`, and MUST NOT block extraction from completing — it runs *after* extraction's success result.
- **FR-012**: The enrichment MUST be skippable via `AT_BMR__BPCR_SECTIONS_ENABLED=false`. When skipped, no `section_id` is set on any extracted page; existing rules behave exactly as today.
- **FR-013**: When the enrichment fails for a single BPCR document (timeout, parse error, missing OCR layout), the run MUST continue. Other documents in the package MUST still receive their normal processing, and the failure MUST be visible in the run log.

### Rule contract update

- **FR-014**: The rule schema SHALL gain an optional `section_id` field on `page_selector`. The bump is **additive** — existing v1.0 rules MUST validate unchanged. Schema version bumps from 1.0 → 1.1.
- **FR-015**: When a rule's `page_selector` declares a `section_id`, the engine SHALL match only `ExtractedPage` rows whose `section_id` equals the rule's `section_id`. Pages with no section assignment (BPCR sections disabled, or no detection ran) MUST NOT match.
- **FR-016**: When section detection is disabled or returned no result for the BPCR but a rule still references `section_id`, the engine SHALL apply the rule's existing `fallback` policy (`flag_as_unevaluated` | `flag_as_indeterminate` | `treat_as_pass`). No new fallback kind is introduced.
- **FR-017**: The rule diff tool (`bmr-rules diff`) SHALL recognise `page_selector.section_id` as a semantically meaningful change and tag transitions with `SCOPE_CHANGED` (or a new tag if the call calls for one).

### Reporting & Evidence

- **FR-018**: Every `EvidenceRegion` produced by a finding that cites a sectioned page SHALL carry the page's `section_id`. For pages outside any sectioned document, `section_id` MUST be omitted from the evidence record (no `null` noise in JSON).
- **FR-019**: The audit report renderer SHALL group BPCR findings by detected section when `section_id` is present (no UI-side filtering required). For documents without sections, behaviour is unchanged.

### Performance & Operability

- **FR-020**: The post-extract enrichment for a BPCR up to 35 pages MUST add no more than **1.0 s** at p95 to total run wall time when running in `heuristic` mode.
- **FR-021**: The detector MUST emit an `bpcr_section_detect_duration_seconds` histogram (Spec 006 metrics) labelled `(method, outcome)` where `outcome ∈ {ok, partial, failed}`.
- **FR-022**: The detector MUST emit a structured log line on entry and exit at `info` level, carrying `doc_id`, `pages`, `method`, and `outcome`.

### Out of Scope

- **OOS-001**: Section detection for any document role other than `BPCR`. Explicit non-goal until the client expands scope.
- **OOS-002**: Editing or moving section boundaries via the HITL UI. Detection is read-only in v0.
- **OOS-003**: Cross-document section reconciliation (e.g. matching a BPCR section to an MFR section). Out of v0; a separate spec if/when needed.
- **OOS-004**: VLM-backed detection. Stub only in v0; lit on after the canonical list and the heuristic baseline are validated on real BPCRs.
- **OOS-005**: Schema major bump. The change is additive (`page_selector.section_id` is optional). v1.0 rules continue to validate.

---

## Open Questions (for the call)

1. **Canonical section list.** What sections are in scope? The spec ships `bpcr-section-spec.yaml` with placeholder entries derived from typical pharma BPCR layouts (cover, dispensing, granulation, compression, coating, IPC, yield calculation, packaging, reconciliation, sign-off). Final list is a client decision.
2. **Default mode after the VLM lands.** `heuristic` (cheaper, brittle) vs `hybrid` (heuristic-first, VLM fallback for low-confidence pages). The plan's recommendation is `hybrid`, but the call should confirm the cost envelope.
3. **Behaviour when detection is disabled but a rule uses `section_id`.** The spec implements the rule's existing `fallback` policy. Is that the right escape valve, or do we want a new `policy: skip_when_no_sections` knob?
4. **Where the canonical list lives long-term.** `config/bmr/pilot/` is fine for the pilot. Production may want it under a per-product / per-customer overlay — defer until after the pilot.
5. **HITL on section detection.** Out of v0 by FR-OOS-002. Worth re-visiting after the call if reviewers want to nudge boundaries.

---

## Glossary

- **BPCR** — Batch Production and Control Record. The 1–35-page execution document inside a BMR package.
- **Section** — a named sub-region of the BPCR (e.g. *Material Dispensing*, *Yield Calculation*). Identified by `section_id` (slug) and `display_name` (human-readable).
- **Canonical section list** — the data file listing every section the detector knows about, including regex aliases and detection bands.
- **Detection band** — *top-of-page*, *top-of-table*, or *mid-page* — where on the page the heuristic looks for a section header.
- **Unsectioned** — sentinel `section_id` for BPCR pages where the detector couldn't confidently match any canonical section.
