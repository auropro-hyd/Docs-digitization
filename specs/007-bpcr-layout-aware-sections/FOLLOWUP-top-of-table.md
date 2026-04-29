# Spec 007 follow-up — implement the `top_of_table` band

**Status**: Open. Discovered post-merge of PR #9 during a code audit.
**Owner**: TBD.
**Estimated effort**: 1.5–2 days build + 0.5 day calibration on a real BPCR.

---

## What's missing

Spec 007's data model and detection-method enum both include
`top_of_table` ([data-model.md:84](data-model.md#L84),
[data-model.md:94](data-model.md#L94)). The pilot section spec at
[`backend/config/bmr/pilot/bpcr-section-spec.yaml`](../../backend/config/bmr/pilot/bpcr-section-spec.yaml)
declares `top_of_table` as an allowed band on **12 of 13 sections**.

The heuristic spike (PR #6) shipped without the band actually being
emitted. [`_band_for_y`](../../backend/app/bmr/capabilities/bpcr_section_detect.py#L165-L170)
only returns `"top_of_page"` or `"mid_page"`:

```python
def _band_for_y(y_fraction: float) -> str:
    if y_fraction <= _TOP_BAND_FRACTION:
        return "top_of_page"
    if y_fraction >= _MID_BAND_BOTTOM:
        return "mid_page"
    return "mid_page"
```

Empirically confirmed: `{_band_for_y(y/100) for y in range(101)} == {'top_of_page', 'mid_page'}`.

Result: a section header sitting at the start of a table mid-page —
**Akhilesh's pinned design concern from the 2026-04-28 call** ("section
markers in this BPCR can appear at the top of a table OR mid-page") —
only matches if it also happens to land in the top 20% of the page.

## Why it matters

Real BPCRs frequently start a section at a table boundary mid-page. Per
the 2026-04-28 transcript, *Yield Calculation* sits in the second half
of its page. When the table doesn't reach into the top 20% band:

- `material_dispensing` headers between in-process notes and the
  weighing table → missed.
- `equipment_list` headers introducing the equipment table on a
  shared page → missed.
- `manufacturing_operations` step-instruction tables that start
  partway down a page → missed.

The current spec lists `top_of_table` on these sections precisely
because reviewers and rule authors expect them to match here. Today
they silently degrade to `mid_page` (which requires emphasis) or
`unmatched`.

## Design

### What "top of a table" means in OCR layout

Azure Document Intelligence and the Datalab adapter both return
`OCRWord` records with `bounding_region.{x, y, width, height}`. There
is no first-class table-row clustering on the port — but Stage 3
already has the words in hand.

Two implementation options:

| | Pros | Cons |
|---|---|---|
| **A. Use OCR engine's native table metadata** (`OCRResult.table_metadata`) | Engine has done the layout work; cells/rows already grouped. | Coverage varies — Datalab adapter populates it; some engines don't. The port treats it as `list[dict]` with no schema, so we'd have to introspect. |
| **B. Light row-clustering on `OCRWord` y-baselines** ✅ | Engine-agnostic; we already do this for line detection in [`_page_lines`](../../backend/app/bmr/capabilities/bpcr_section_detect.py#L124-L162). Extend it to also flag the *first* row in a contiguous run of densely-aligned rows. | Heuristic on a heuristic — the band-detection precision degrades when the first row has noisy OCR. |

Recommend **B**. Reason: we already extract per-line `(text, y_fraction)`
tuples in `_page_lines`. Extend that pipeline to also tag a row as
`is_table_first_row` when:

1. The current row plus the next 2 rows have ≥3 distinct x-bins each
   (i.e. multi-column rows), AND
2. Inter-row gap is roughly uniform (table rows are evenly spaced), AND
3. The current row is preceded by a single-column row (a header sitting
   above a table).

Then `_band_for_y` becomes context-aware:

```python
def _band_for_line(line: _Line) -> str:
    if line.is_table_first_row:
        return "top_of_table"
    if line.y_fraction <= _TOP_BAND_FRACTION:
        return "top_of_page"
    return "mid_page"
```

(Drops the `y_fraction` argument in favour of richer per-line struct.)

### What stays the same

- The `matched_band` enum is already `top_of_page | top_of_table | mid_page`.
- The `detection_method` already includes `heuristic_top_of_table`.
- `requires_emphasis_for_mid_page` semantics (yield_calculation in the
  spec) untouched.
- Confidence assignments at [bpcr_section_detect.py:243-253](../../backend/app/bmr/capabilities/bpcr_section_detect.py#L243-L253)
  need a new branch: `top_of_table` should rank between `top_of_page`
  and `mid_page` (e.g. `_CONF_TOP_OF_TABLE = 0.85`).

## Acceptance criteria

1. `_band_for_y` (or its replacement) returns `"top_of_table"` for at
   least one input case backed by a synthetic OCR fixture.
2. A section whose `bands` includes `top_of_table` and whose header
   text appears as the first row of a table on a mid-page band gets
   detected with `detection_method="heuristic_top_of_table"` and
   `matched_band="top_of_table"`.
3. The existing 5 detector tests keep passing — no regression on
   `top_of_page` and `mid_page` bands.
4. New fixture test against a real-shaped BPCR page (Datalab JSON
   from one pilot doc) shows `material_dispensing` and one of
   `equipment_list` / `manufacturing_operations` detected via
   `top_of_table` where they previously fell through to `unmatched`.
5. `RunReport.bpcr_sections` rows show `detection_method:
   "heuristic_top_of_table"` for the relevant pages so reviewers
   can audit the table-band signal.

## Out of scope

- VLM mode (still Phase 2 per the original plan).
- Detection across page boundaries (continuation pages of a multi-page
  table) — that's its own follow-up.
- The case where a section header spans two visual lines (rare in
  the pilot sample; revisit when one shows up).

## Open questions

1. **Confidence value for `top_of_table`** — proposal: `0.85`
   (between `_CONF_PRIMARY_TOP=1.0` and `_CONF_MID_PAGE=0.6`).
   Calibrate after first real BPCR.
2. **Threshold tuning for "table" detection** — the 3 conditions
   above (≥3 columns × 3 rows × uniform gap) are first-cut. Real
   BPCRs may have 2-column tables (e.g. Equipment ID / Description).
   Plan to instrument with a structured log
   (`bpcr.section_detect.table_detected n_rows=… n_cols=…`) and
   relax thresholds based on observed counts.
3. **Fallback when `OCRPageResult.words` is empty** (markdown-only
   path used by SidecarExtractor's synthesised OCR) — `top_of_table`
   simply unavailable; degrades to existing `top_of_page` /
   `mid_page` behaviour. Document this explicitly in the
   `_synthesize_ocr_from_extracted` docstring so operators understand
   the precision loss when no sidecar exists.

## Sequencing

Lands as a small focused PR (call it PR #11 when picked up). No new
spec needed — existing Spec 007 contracts already accommodate this. A
single research-note paragraph in [research.md](research.md) would be
nice for posterity but not required.

This work blocks **nothing** — section detection currently works at
`top_of_page` precision and PR #10 surfaces detection metadata so
reviewers can spot the gap. Pick up when you have a real Datalab JSON
sample to calibrate against; otherwise we'd be tuning thresholds on
synthetic data.
