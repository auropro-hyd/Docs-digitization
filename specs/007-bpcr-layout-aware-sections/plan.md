# Implementation Plan: BPCR Layout-Aware Section Detection

**Branch**: `feat/007-bpcr-layout-aware-sections` | **Date**: 2026-04-29 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/007-bpcr-layout-aware-sections/spec.md`

## Summary

Add a single new BMR capability — `detect_bpcr_sections` — that takes the OCR layout for a document tagged `BPCR` and returns a `BPCRSectionMap` (per-page `section_id` assignments). Run it as a *post-extraction enrichment* on every BMR run: extraction completes first, then a tagger walks the resulting `ExtractedPackage` and stamps `section_id` on each `ExtractedPage` belonging to a BPCR document. Extend `page_selector` in the rule schema (1.0 → 1.1, additive) to accept an optional `section_id` filter; rules that don't use it behave identically. Surface the section on `EvidenceRegion`.

The v0 spike ships **heuristic mode only** (regex + word-position over OCR layout) — fast, deterministic, no extra external dependencies. The `vlm` and `hybrid` paths are scaffolded as `NotImplementedError` and gated on the canonical section list being locked on the call.

## Technical Context

- **Language/Version**: Python 3.13 (backend). No frontend changes in v0 — section grouping in the report renderer is server-side projection.
- **Primary Dependencies**: existing — `pydantic`, `jsonschema`, `pyyaml`, `app/core/ports/ocr.py`. **No new deps.**
- **Storage**: none new. `section_id` lives on the in-memory `ExtractedPage` and on the persisted `EvidenceRegion`.
- **Testing**: `pytest` (existing harness). Unit tests for the heuristic detector, integration tests for the tagger + the rule-engine `section_id` selector, end-to-end test for the back-compat invariant.
- **Target Platform**: same backend container — no new processes.
- **Performance Goals**: ≤1.0 s p95 extra wall time for a 35-page BPCR in `heuristic` mode. Sectioning a 35-page BPCR shouldn't allocate more than ~5 MB.
- **Constraints**: capability does not import FastAPI, LangGraph, or the run service (Constitution VII). The canonical section list is pure data; capability is pure function over `(OCRResult, sections_spec)`.
- **Scale/Scope**: 1 BPCR per package, ~35 pages typical. Worst-case bounded by `MAX_OCR_PAGES_PER_DOC` (already env-configurable).

## Constitution Check

Reference: `.specify/memory/constitution.md` (v1.1.0).

- [x] **I. Leverage-first**: Reuses `app/compliance/segmentation.py` (untouched, still does document-boundary detection), `app/core/services/section_builder.py` (heuristic primitives reused for header detection), `OCRResult` shape, the existing `page_selector` machinery, and the existing fallback policy.
- [x] **II. 5-stage soft gates + parallel compliance**: No new stage. The enrichment hangs off the **end** of Stage 3 (extraction), runs once before Stage 4 (compliance) starts, and is a no-op when no BPCR is present. Stage topology is unchanged.
- [x] **III. Capability-first**: `detect_bpcr_sections` is a single function; tagging the package is a separate function; rule-time matching is a one-line addition to the existing `page_selector` resolver. No god class.
- [x] **IV. Single final checkpoint & selective re-run**: Section assignments are deterministic per `(OCRResult, sections_spec)`. A correction-driven re-run with the same OCR + spec produces the same sections; the existing `RerunPlan` continues to work.
- [x] **V. Evidence-bound findings**: `EvidenceRegion` is *extended* (additive) with optional `section_id`. No existing evidence drops or re-shapes.
- [x] **VI. Configurable framework**: Enable/disable via `AT_BMR__BPCR_SECTIONS_ENABLED`. Mode via `AT_BMR__BPCR_SECTIONS_MODE`. Canonical list via the YAML file path `AT_BMR__BPCR_SECTIONS_SPEC` (defaults to `config/bmr/pilot/bpcr-section-spec.yaml`). No client-specific Python code.
- [x] **VII. Existing framework is the backbone**: Capability lives in `app/bmr/capabilities/`, has zero imports from `app/api/`, `app/compliance/`, or `app.bmr.workflow.service`. The wiring lives in the workflow stage; the capability is pure.
- [x] **VIII. ALCOA+ audit trail**: Every section assignment carries the `detection_method` (`heuristic_top_of_page`, `heuristic_top_of_table`, `heuristic_mid_page`, `vlm_top_band`, `vlm_mid_band`, `unmatched`) — fully attributable. The capability emits a structured log line on entry and exit (Spec 006 hooks).
- [x] **IX. Rule-as-data**: The canonical section list is YAML, not Python. Schema bump 1.0 → 1.1 is published alongside the docgen-regenerated markdown and a CHANGELOG entry.

No violations. No Complexity Tracking entries.

## Project Structure

```
backend/
  app/
    bmr/
      capabilities/
        bpcr_section_detect.py   # NEW — pure function: OCRResult + spec → BPCRSectionMap
        bpcr_section_tagger.py   # NEW — pure function: ExtractedPackage + BPCRSectionMap → ExtractedPackage'
        evidence.py              # MODIFIED — EvidenceRegion gains optional section_id
        extracted_data.py        # MODIFIED — ExtractedPage gains optional section_id
        rule_eval.py             # MODIFIED — page_selector matching honours section_id when set
      rules/
        loader.py                # unchanged
        validator.py             # unchanged (jsonschema-driven; new field is additive)
      workflow/
        stages.py                # MODIFIED — make_extraction_stage tail-calls bpcr_section_tagger
        service.py               # MODIFIED — wires the section spec loader at construction
    config/
      settings.py                # MODIFIED — AT_BMR__BPCR_SECTIONS_* flags
  config/
    rules/
      schema/
        rule.schema.v1.1.json    # NEW — additive bump (section_id under page_selector)
        rule.schema.v1.1.md      # NEW — generated by docgen
        CHANGELOG.md             # MODIFIED — 1.1 entry
    bmr/
      pilot/
        bpcr-section-spec.yaml   # NEW — placeholder canonical list (to be locked on the call)
        bank/
          alcoa_accurate_bpcr_yield_calc.yaml  # NEW — example v1.1 rule using section_id
  tests/
    bmr/
      capabilities/
        test_bpcr_section_detect.py  # NEW — heuristic detector unit tests
        test_bpcr_section_tagger.py  # NEW — tagger unit tests
        test_rule_eval_section.py    # NEW — section_id selector tests + back-compat
      workflow/
        test_section_enrichment.py   # NEW — integration: extraction → tagger → compliance
      rules/
        test_schema_v1_1.py          # NEW — schema parity + back-compat
specs/007-bpcr-layout-aware-sections/
  spec.md
  plan.md
  research.md
  data-model.md
  contracts/
    capability-contract.md
    section-spec-config.md
  quickstart.md
```

## Phasing

### Phase 1 — Spike: heuristic detection end-to-end (this PR)

Deliverable: a BPCR-only, heuristic-only path that runs in production by default, gated by an env flag.

1. **Capability** — `detect_bpcr_sections(ocr: OCRResult, *, sections_spec: BPCRSectionsSpec) -> BPCRSectionMap`. Pure function. Returns one `SectionSpan` per detected section, plus `unsectioned` filler spans.
2. **Tagger** — `tag_bpcr_pages(package: ExtractedPackage, *, section_maps: dict[str, BPCRSectionMap]) -> ExtractedPackage`. Walks pages, assigns `section_id`. Returns a *new* package (frozen models, no mutation).
3. **Enrichment hook** — at the end of `make_extraction_stage`, after the extractor returns, look up the BPCR `DocumentRef`(s); for each, run OCR (cached) → detector → tag the package.
4. **Schema 1.1** — copy `rule.schema.v1.0.json` → `rule.schema.v1.1.json`, add `section_id` (string, slug pattern) under `page_selector`. v1.0 stays available; rules can pin either.
5. **Selector** — extend `_select_pages_for_aggregate` (and the equivalent same-page helper) to honour `section_id` when set.
6. **Evidence** — add `section_id: str | None = None` to `EvidenceRegion`. Capabilities populate it from the matched page when present.
7. **Pilot rule** — one example v1.1 rule under `config/rules/pilot/bank/alcoa_accurate_bpcr_yield_calc.yaml` so the new selector is exercised by `bmr-rules fixture-run`.
8. **Tests** — see Project Structure.

### Phase 2 — VLM mode (post-call)

Out of scope for this PR. Plan summary so the call is informed:

- Implement `vlm` mode behind the same `detect_bpcr_sections` interface, dispatching to a small per-page VLM call against cropped top + middle bands of each page (rather than the whole page). Rough cost target: ≤10 VLM calls per 35-page BPCR (only pages where heuristic was unsure).
- `hybrid` mode = heuristic first, VLM only on heuristic-unsure pages. Becomes the default once latency + accuracy numbers land.
- Add `bpcr_section_detect_method` label to the metrics histogram so we can monitor the heuristic/VLM mix in production.

### Phase 3 — Reviewer UX (separate spec if needed)

- Section grouping in the report viewer.
- Optional HITL action to re-assign a page to a different section.

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Heuristic mis-tags pages → wrong findings fire | M | M | `AT_BMR__BPCR_SECTIONS_ENABLED=false` rollback; rules referencing `section_id` apply their fallback policy when sections are missing; `unsectioned` sentinel is explicit so reviewers see the gap |
| Canonical section list drifts from real BPCRs | H | M | YAML versioning + CHANGELOG; spec-loader rejects unknown sentinel collisions; hybrid mode (post-PR) catches drift |
| Detector adds latency | L | L | Heuristic is regex over already-OCR'd words; FR-020 budget of 1.0s p95 is generous; metric histogram surfaces regressions |
| Schema bump breaks existing rules | L | H | Bump is purely additive; v1.0 schema retained and serves any v1.0-pinned rule unchanged; test_schema_v1_1.py asserts the back-compat invariant |
| Section detection blows up the run | L | H | Capability fails open per FR-006 — exception → single `unsectioned` span + warning log; run continues |

## Test Plan

- `uv run pytest tests/bmr/capabilities/test_bpcr_section_detect.py` — heuristic correctness for top-of-page, top-of-table, mid-page headers; deterministic output; exception handling.
- `uv run pytest tests/bmr/capabilities/test_bpcr_section_tagger.py` — frozen-model immutability; partial maps; non-BPCR roles untouched.
- `uv run pytest tests/bmr/capabilities/test_rule_eval_section.py` — `section_id` filter applied / not applied; fallback policy when sections absent; v1.0 rule unaffected.
- `uv run pytest tests/bmr/workflow/test_section_enrichment.py` — full extraction → enrichment → compliance path on a fixture with a known-good `section_id` distribution.
- `uv run pytest tests/bmr/rules/test_schema_v1_1.py` — v1.0 rule validates against v1.1 schema; v1.1 rule with `section_id` rejects on v1.0 schema; docgen output is fresh.
- `uv run bmr-rules validate config/rules/pilot/bank` — pilot bank still validates (v1.0 + v1.1 rules together).
- `uv run bmr-rules fixture-run --rule config/rules/pilot/bank/alcoa_accurate_bpcr_yield_calc.yaml --fixture tests/bmr/fixtures/rules/fixtures/bpcr_section_aware.json` — the new selector fires against the new fixture.
- `uv run ruff check app/bmr/ tests/bmr/` — clean.

## Rollout

1. Land this PR with `AT_BMR__BPCR_SECTIONS_ENABLED=true` as the default. Existing rules behave identically because none of them reference `section_id` yet.
2. Author the canonical section list with the client (post-call) and replace the placeholder `bpcr-section-spec.yaml`.
3. Convert one or two existing aggregate rules to use `section_id` and run side-by-side against a real BPCR; confirm finding parity.
4. Open Phase 2 PR to add `vlm`/`hybrid` modes.
