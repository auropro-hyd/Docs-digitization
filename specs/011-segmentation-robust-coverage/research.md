# Research: Robust Segmentation Coverage

**Feature**: 011-segmentation-robust-coverage
**Created**: 2026-05-14
**Method**: Inversion thinking — instead of asking "how do we make segmentation good?", we asked "what could cause segmentation to fail?" and then designed defensively for each failure mode.

## What's already covered by PR #69

PR #69 introduced three deterministic post-processes that closed the geometric- and vocabulary-level failures the user observed on 2026-05-13:

| Failure mode | PR #69 mitigation |
|---|---|
| Hallucinated page ranges (`BPCR Review Checklist` at p1-2 when doc is 115 pages) | `clamp_page_ranges()` — clip end_page > total_pages, drop start_page > total_pages, clip negative start_page |
| Overlapping sections (75-87 and 80-98 double-counted) | `resolve_overlaps()` — walk sorted sections; clamp later section past earlier's end; drop fully contained dupes |
| `section_type='unsectioned'` leaking to compliance pipeline | `normalize_section_types_to_canonical()` — fold drift via aliases; collapse non-canonical to `"unknown"` |
| SCADA cluster `data_monitoring_parameters` / `alarm_log` non-canonical | Same normaliser + new YAML aliases under `section_aliases` |
| Raw material `section_type == document_type` echo | Stronger prompt rule 6 + new `raw_material_request` sub-section types (`packing_material_request`, `solvent_transfer_note`) |

## Inversion analysis — what could still cause failure

We catalogued every realistic failure mode at each pipeline stage. The catalogue informed FR-001 through FR-014.

### A. Input-side (data the LLM never sees)

**A.1 Page summary cap at 500 chars.** Section headers sometimes sit below a preceding table or image — past byte 500 — and the LLM doesn't see them. Mitigated by the existing 2026-05-12 prompt heuristics ("Check List for X Operations" header cues) but not robust to layout variation. **Out of scope** for this spec — the more impactful fix is to use the page-header bytes specifically (FR-001) rather than read more body content.

**A.2 Page-header "Page X of Y" signal ignored.** Most pharma forms print this in their running header. It's the highest-confidence document-boundary signal available — the form designer literally printed how many pages it has. **Closed by FR-001 through FR-005.**

**A.3 No use of `key_value_pairs` for cross-validation.** KV pairs are per-page metadata (batch number, product name, equipment ID). A section claiming 10 pages with zero KV pairs across them is almost certainly mis-classified. **Closed by FR-010.**

**A.4 Image-only pages.** OCR markdown is empty; the LLM has nothing to classify against and the page becomes a silent gap. Already mitigated by `fill_gaps_with_unknown` from PR #45; FR-010 will also catch them as `no_kv_evidence`.

### B. LLM output we accept as-is when we shouldn't

**B.1 Output truncation.** `generate_structured` on a 150-page packet may hit the LLM's output token cap mid-JSON. Pydantic still parses the partial response; the pipeline accepts an incomplete segmentation. **Closed by FR-006, FR-007, FR-008.**

**B.2 No per-section confidence.** The LLM returns one `confidence` for the whole segmentation, not per-section. **Out of scope** for this spec — would require schema changes to `DocumentSegmentation` model. Tagged as a follow-up.

**B.3 Non-determinism across re-runs.** Same input → different output. Mitigated by `temperature=0` in the LLM call (already in place via the structured-output adapter). FR-013's overrides-preservation gives operators a "lock the known-good answer" mechanism that survives any variance.

**B.4 Structural absurdities.** A `batch_record` with zero `cover_page` is impossible in the real document; we'd accept it today. **Closed by FR-009.**

**B.5 Cross-field inconsistency.** `section_type='manufacturing_operations'` paired with `document_type='ipc_report'` is contradictory. **Closed by FR-011.**

### C. Post-process gaps in our code (today, after PR #69)

**C.1 Gap-fill masks real LLM failure.** `fill_gaps_with_unknown` plugs holes silently. A 50-page `unknown` block is a regulator-visible blackout, but it doesn't fail the run. **Addressed indirectly by FR-006** — coverage shortfall ≥3% is treated as truncation worth retrying.

**C.2 No dedupe by (section_type, doc_type, first_page).** Two `cover_page` sections in the same BPCR is impossible; we'd keep both. PR #69's overlap resolver handles overlapping ranges; for non-overlapping duplicates (e.g. `cover_page` at p1 AND at p50), the structural-minimum check (FR-009) catches the case where required sections appear *too many* times if we extend the validator to compare emitted-counts against the YAML — **deferred for v2** if needed.

### D. Workflow & persistence

**D.1 `/segment` route wipes HITL edits.** Every re-segmentation overwrites `segmentation.json`; operator edits are lost. **Closed by FR-012, FR-013.**

**D.2 No diff between runs.** Operator has no way to spot what changed after a re-segmentation. **Out of scope** for this spec — UI work. The `validation_issues` array from FR-014 gives a first-cut signal (what's wrong now), but a true diff view needs frontend treatment.

### E. Compliance pipeline integration

**E.1 Compliance rules apply only to known section_types.** PR #69's normaliser already closes drift; the `validation_issues` array from FR-014 surfaces `type_mismatch` for the remaining canonical-but-wrong-document-type cases.

**E.2 BPCR sub-section enrichment fails silently.** When the heuristic detector misses spans, the parent BPCR stays as one block. The existing `enrich_with_bpcr_sub_sections` logs at WARNING level; operators already see this. **Out of scope** for this spec.

## Why a deterministic post-process, not a better prompt

The user has already explored prompt-tightening (the 2026-05-12 prompt heuristics). LLM behaviour remains non-deterministic and the cost of every prompt-cue iteration is high (operator has to re-run on real documents, eyeball the output). Three of the five gaps in this spec — page-header boundary, structural minimums, KV-pair cross-evidence — admit purely-deterministic detection from data we already have on disk:

- Page-header text is in the OCR markdown.
- Required sections are in `document_profiles.yaml`.
- KV pairs are in `result.json`.

A deterministic post-process is:

- **Robust**: same input → same output, every run.
- **Cheap**: pure CPU, runs in milliseconds; no LLM round-trip.
- **Observable**: every correction emits a telemetry event with the original / corrected values so HITL reviewers can see what was changed.
- **Testable**: every transition is a unit-test fixture.

The truncation-retry path (FR-007) does involve a second LLM call, but it's gated on a quantitative coverage shortfall (not a guess) and capped at 2 attempts (not infinite). The alternative — accepting a silently incomplete segmentation — is worse.

## Why operator-override sidecar, not in-place mutation

We considered three storage shapes for FR-012:

| Option | Pros | Cons | Decision |
|---|---|---|---|
| In-place mutation of `segmentation.json` | Simplest; today's behaviour | Re-segmentation overwrites it; no audit trail of who / when changed what | Reject |
| Operator edits flagged in-place (`operator_modified: true`) | Survives reads; trivially diffable | Conflates LLM output with HITL edits; complicates re-segmentation logic ("which fields can I overwrite?") | Reject |
| Sidecar file `segmentation.overrides.json` | Clean separation; carries timestamp + actor; re-segmentation is unchanged | One more file to reason about | **Accepted** |

The sidecar shape also gives us a natural place to record `recorded_at` and `actor` per override — useful for compliance audit trail (Principle VIII).

## Threshold choice: 0.97 coverage for truncation detection

`fill_gaps_with_unknown` plugs LLM-left gaps with `section_type='unknown'`. Some real runs legitimately have a few uncovered pages (e.g. a blank cover-letter page); these aren't truncations. The 0.97 threshold (3% gap) was picked because:

- On a 100-page packet, 3% = 3 pages, comparable to typical legitimate gaps.
- On a 200-page packet, 3% = 6 pages — still small enough that a real truncation (LLM lost the tail 100 pages) shows up loudly.
- Below 0.97 with `finish_reason='length'` is a high-confidence truncation; below 0.97 alone is a softer signal worth investigating.

We expect to tune this once we have real-doc telemetry. The threshold is a single `_TRUNCATION_COVERAGE_THRESHOLD` constant for easy adjustment.

## Failure modes deliberately NOT addressed in this spec

| Failure mode | Why deferred |
|---|---|
| Per-section LLM confidence | Schema change to `DocumentSegmentation`; bigger blast radius |
| Diff view between re-segmentations | Frontend work; orthogonal to the backend fixes |
| Image-only-page classification | OCR/VLM work, not segmentation work |
| BPCR sub-section detector improvements | Already covered by Spec 007; this spec stays out of its scope |
| Document boundary signals beyond `Page X of Y` (e.g. header logo changes, page-restart heuristics) | Diminishing returns vs the explicit `Page X of Y` signal; revisit if FR-001 isn't enough |
| Frontend `validation_issues` rendering | Wire-only change in FR-014; UI follows in a separate PR |

## Open questions to verify during implementation

- **Q-001**: How tolerant should the `Page X of Y` regex be? Strict (`Page X of Y`) catches most cases; tolerant (`[Pp]a?ge ?\d+ ?(of|/) ?\d+`) catches OCR typos. Decision: tolerant with a `confidence` field so HITL can act on low-confidence parses (FR-001).
- **Q-002**: Should the boundary-aware merge happen before or after `resolve_overlaps`? **Decision**: AFTER — `resolve_overlaps` handles within-document overlaps; the boundary merger handles a different class (LLM mis-split). They compose cleanly when run in order.
- **Q-003**: What's the cap on retry attempts (FR-008)? **Decision**: 2. First retry recovers a typical truncation; a second-retry truncation is symptomatic of a deeper issue (LLM context overflow on a single-half) and should bubble to HITL rather than loop.
- **Q-004**: Should `validation_issues` from `validate_segmentation` go in the existing structured-issues list or in a new field? **Decision**: existing list (extend kinds). One stream is easier for the UI to render and for telemetry to aggregate.
