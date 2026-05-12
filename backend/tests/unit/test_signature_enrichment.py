"""Pin the four-layer signature-enrichment contract.

The enricher is the deterministic post-OCR pass that restores
``[Signature]`` markers in cells where Datalab's classifier
missed the stroke but context (signature-named column + cell
with handwritten content) makes presence obvious.

The load-bearing invariants pinned here:

1. **L0/L1 backward compatibility** — Datalab markers that were
   already there are NEVER touched, and the per-layer telemetry
   counts them.
2. **L2 path** — Signature block in JSON tree + signature-named
   column → inject ``[Signature]`` at confidence 0.65.
3. **L3 path** — Handwriting block (no Signature classification)
   + signature-named column → inject at confidence 0.45.
4. **Idempotency** — running ``enrich_page`` twice produces the
   same output. Running on a cell that already has the marker
   skips it (with a counted ``skipped_idempotent``).
5. **Conservative defaults** — empty cells in signature columns
   are NEVER stamped (an empty Done-by cell is a legitimate
   missing-signature finding; the enricher must not paper over
   it).
6. **Kill switch** — ``enabled=False`` returns the markdown
   unchanged but still computes L0/L1 telemetry, so an A/B
   diagnostic comparison against the raw classifier is one
   flag flip away.
7. **Page isolation** — a Handwriting block on page 3 must
   not produce a Signature marker on page 5.
"""

from __future__ import annotations

import pytest

from app.adapters.ocr.signature_enricher import (
    EXISTING_MARKER_RE,
    JsonBlock,
    _bbox_contains,
    _enumerate_tables,
    _is_date_only_or_empty,
    _is_signature_column_header,
    enrich_page,
)


# ── Reusable fixtures ────────────────────────────────────────


_SIGNATURE_COLUMNS = (
    "done by",
    "checked by",
    "verified by",
    "signed by",
    "operator",
    "initials",
    "sign",
    "signature",
)


def _poly(x1: float, y1: float, x2: float, y2: float) -> tuple[tuple[float, float], ...]:
    """Build a rectangular polygon from corners — readable in tests."""
    return ((x1, y1), (x2, y1), (x2, y2), (x1, y2))


def _signature_block(x1: float = 100, y1: float = 100, x2: float = 200, y2: float = 130, page_num: int = 3) -> JsonBlock:
    return JsonBlock(
        block_type="Signature", polygon=_poly(x1, y1, x2, y2),
        page_num=page_num, text="",
    )


def _handwriting_block(x1: float = 100, y1: float = 100, x2: float = 200, y2: float = 130, page_num: int = 3) -> JsonBlock:
    return JsonBlock(
        block_type="Handwriting", polygon=_poly(x1, y1, x2, y2),
        page_num=page_num, text="initials",
    )


# ── L0 / L1: backward compatibility ──────────────────────────


def test_l1_existing_markers_are_counted_not_modified() -> None:
    md = """\
| Step | Done by | Checked by |
|------|---------|------------|
| 1 | [Signature]\n03/10/2025 | [Signature]\n03/10/2025 |
"""
    result = enrich_page(md, [], page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)
    assert result.markdown == md, "existing [Signature] markers must not be touched"
    assert result.telemetry.layer_counts["L1"] == 2
    assert result.telemetry.injected_count == 0


def test_l0_block_comments_are_counted() -> None:
    md = "<!-- block_type: Signature -->\nFoo bar"
    result = enrich_page(md, [], page_num=1, signature_column_headers=_SIGNATURE_COLUMNS)
    assert result.telemetry.layer_counts["L0"] == 1
    assert result.markdown == md


# ── L2: bbox-precise injection (Datalab classified Signature) ─


def test_l2_signature_block_in_table_injects_marker() -> None:
    """Page has a Datalab-classified Signature block. The
    enricher trusts that classification: cells in signature
    columns with date-only content are stamped with
    ``[Signature]`` at L2 confidence."""
    md = """\
| Step | Done by | Checked by |
|------|---------|------------|
| 1 | 03/10/2025 | 03/10/2025 |
"""
    blocks = [_signature_block(page_num=3)]
    result = enrich_page(md, blocks, page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)

    assert "[Signature]" in result.markdown
    assert result.telemetry.layer_counts["L2"] == 2  # Done by + Checked by
    assert result.telemetry.layer_counts["L3"] == 0
    assert result.telemetry.injected_count == 2


# ── L3: heuristic injection (Handwriting only, no Signature) ─


def test_l3_handwriting_only_triggers_at_lower_confidence() -> None:
    """The new-doc symptom: Datalab classified handwriting blocks
    but emitted zero Signature blocks. L3 fills in at lower
    confidence so the next layer (rule 5 / HITL) can still
    treat them as candidate signatures."""
    md = """\
| Step | Done by | Checked by |
|------|---------|------------|
| 1 | 03/10/2025 | 03/10/2025 |
"""
    blocks = [_handwriting_block(page_num=3)]
    result = enrich_page(md, blocks, page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)

    assert "[Signature]" in result.markdown
    assert result.telemetry.layer_counts["L3"] == 2
    assert result.telemetry.layer_counts["L2"] == 0


def test_l2_outranks_l3_when_both_signals_present() -> None:
    """When a page has both Signature and Handwriting blocks, the
    Signature classification wins — L2 fires, not L3."""
    md = """\
| Step | Done by |
|------|---------|
| 1 | 03/10/2025 |
"""
    blocks = [_signature_block(page_num=3), _handwriting_block(page_num=3)]
    result = enrich_page(md, blocks, page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)

    assert result.telemetry.layer_counts["L2"] == 1
    assert result.telemetry.layer_counts["L3"] == 0


# ── Conservative defaults ────────────────────────────────────


def test_empty_cell_in_signature_column_is_never_stamped() -> None:
    """An empty Done-by cell is a legitimate missing-signature
    finding. The enricher must NOT paper over it.

    This is the most load-bearing test in this file — without
    it, a strict reading of "page has handwriting → mark all
    cells" would synthesize false-positive signatures into
    cells that the operator legitimately forgot to sign.
    """
    md = """\
| Step | Done by |
|------|---------|
| 1 |  |
"""
    blocks = [_signature_block(page_num=3)]
    result = enrich_page(md, blocks, page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)

    assert "[Signature]" not in result.markdown, (
        "empty signature-column cell must remain empty — that's the "
        "missing-signature finding rule 5 will surface"
    )
    assert result.telemetry.injected_count == 0


def test_non_signature_column_is_never_touched() -> None:
    """A handwriting block on the page must NOT poison cells in
    non-signature columns (e.g. ``Net Qty`` with handwriting)."""
    md = """\
| Step | Net Qty | Done by |
|------|---------|---------|
| 1 | 03/10/2025 | 03/10/2025 |
"""
    blocks = [_handwriting_block(page_num=3)]
    result = enrich_page(md, blocks, page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)

    # Only Done by gets the marker; Net Qty doesn't.
    lines = [r for r in result.markdown.split("\n") if "03/10/2025" in r]
    assert len(lines) == 1
    parts = lines[0].split("|")
    # Cell 0 = "", 1 = " 1 ", 2 = Net Qty, 3 = Done by, 4 = ""
    assert "[Signature]" not in parts[2], "Net Qty column must not be stamped"
    assert "[Signature]" in parts[3], "Done by column must be stamped"


def test_l4_fires_when_no_block_evidence_but_aggressive_is_on() -> None:
    """The May 4 diagnostic showed Datalab returns
    ``handwritten_count=0`` on every page even when 12+
    ``[Signature]`` markers were inline. The JSON tree is
    unreliable as block evidence; L4 closes the gap by firing
    on column-header + date alone.

    THIS IS THE PATH AKHILESH'S NEW DOC NEEDS — without it the
    enricher does nothing because Datalab emits no Handwriting
    or Signature blocks for that doc."""
    md = """\
| Step | Done by |
|------|---------|
| 1 | 03/10/2025 |
"""
    result = enrich_page(md, [], page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)
    assert "[Signature]" in result.markdown, (
        "L4 must fire on column-header + date alone when aggressive=True"
    )
    assert result.telemetry.layer_counts["L4"] == 1
    assert result.telemetry.injected_count == 1


def test_l4_does_not_fire_when_aggressive_is_off() -> None:
    """The strict "trust Datalab only" mode: if no JSON-tree
    evidence, no injection. This is the original PR #32
    behavior, preserved as an opt-out for diagnostic A/B and
    for users who want classifier-only semantics."""
    md = """\
| Step | Done by |
|------|---------|
| 1 | 03/10/2025 |
"""
    result = enrich_page(
        md, [], page_num=3,
        signature_column_headers=_SIGNATURE_COLUMNS, aggressive=False,
    )
    assert "[Signature]" not in result.markdown
    assert result.telemetry.injected_count == 0
    assert result.telemetry.layer_counts["L4"] == 0


# ── Idempotency ──────────────────────────────────────────────


def test_idempotent_under_double_enrichment() -> None:
    md = """\
| Step | Done by |
|------|---------|
| 1 | 03/10/2025 |
"""
    blocks = [_signature_block(page_num=3)]
    once = enrich_page(md, blocks, page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)
    twice = enrich_page(once.markdown, blocks, page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)

    assert once.markdown == twice.markdown
    # The second pass sees the marker the first pass injected
    # and skips, counted under skipped_idempotent.
    assert twice.telemetry.skipped_idempotent >= 1
    assert twice.telemetry.injected_count == 0


def test_already_stamped_cell_is_not_double_stamped() -> None:
    md = """\
| Step | Done by | Checked by |
|------|---------|------------|
| 1 | [Signature] 03/10/2025 | 03/10/2025 |
"""
    blocks = [_signature_block(page_num=3)]
    result = enrich_page(md, blocks, page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)

    # Done by already had [Signature] — skipped. Checked by gets it.
    sig_count = len(EXISTING_MARKER_RE.findall(result.markdown))
    assert sig_count == 2  # 1 pre-existing + 1 injected, NOT 3
    assert result.telemetry.layer_counts["L2"] == 1
    assert result.telemetry.skipped_idempotent == 1


# ── Kill switch ──────────────────────────────────────────────


def test_kill_switch_disables_injection_but_keeps_telemetry() -> None:
    md = """\
| Step | Done by |
|------|---------|
| 1 | 03/10/2025 |
| 2 | [Signature] 04/10/2025 |
"""
    blocks = [_signature_block(page_num=3)]
    result = enrich_page(
        md, blocks, page_num=3,
        signature_column_headers=_SIGNATURE_COLUMNS, enabled=False,
    )

    assert result.markdown == md, "kill switch on means no markdown change"
    # L1 telemetry still computed.
    assert result.telemetry.layer_counts["L1"] == 1
    # L2/L3 NOT counted because injection didn't happen — the
    # counts reflect what was actually injected, not what would
    # have been.
    assert result.telemetry.layer_counts["L2"] == 0
    assert result.telemetry.layer_counts["L3"] == 0


# ── Page isolation ───────────────────────────────────────────


def test_handwriting_block_on_other_page_does_not_pull_signature_layer() -> None:
    """A Handwriting block on page 5 must NOT cause page 3 to
    fire at L3 — only at L4 (since page 3 has no own evidence)."""
    md = """\
| Step | Done by |
|------|---------|
| 1 | 03/10/2025 |
"""
    # Block is on page 5, we're enriching page 3 with aggressive
    # mode OFF so L4 doesn't backfill.
    blocks = [_handwriting_block(page_num=5)]
    result = enrich_page(
        md, blocks, page_num=3,
        signature_column_headers=_SIGNATURE_COLUMNS, aggressive=False,
    )
    assert "[Signature]" not in result.markdown
    # With aggressive ON (default), page 3 lands at L4 not L3,
    # because L3 requires page-3's own handwriting block.
    result_agg = enrich_page(
        md, blocks, page_num=3, signature_column_headers=_SIGNATURE_COLUMNS,
    )
    assert result_agg.telemetry.layer_counts["L3"] == 0
    assert result_agg.telemetry.layer_counts["L4"] == 1


# ── Configuration edge cases ─────────────────────────────────


def test_empty_column_header_config_disables_enrichment() -> None:
    md = """\
| Step | Done by |
|------|---------|
| 1 | 03/10/2025 |
"""
    blocks = [_signature_block(page_num=3)]
    result = enrich_page(md, blocks, page_num=3, signature_column_headers=())
    assert "[Signature]" not in result.markdown


def test_column_header_match_is_substring_and_case_insensitive() -> None:
    """``DONE BY`` (uppercase) and ``done by`` and
    ``Operator Done By`` should all match. ``done`` (substring
    of ``Done by``) is also in the config and matches anything
    containing 'done'."""
    md = """\
| Step | DONE BY | Operator Initial |
|------|---------|------------------|
| 1 | 03/10/2025 | 03/10/2025 |
"""
    blocks = [_signature_block(page_num=3)]
    result = enrich_page(md, blocks, page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)

    # Both columns should match: "DONE BY" matches "done by",
    # "Operator Initial" matches "operator".
    assert result.telemetry.layer_counts["L2"] == 2


def test_normalize_for_match_keeps_dates_for_cell_correlation() -> None:
    """``_normalize_for_match`` is the cell-to-cell matching key
    used by the signature-crop injector. It MUST preserve the
    cell's identity (including dates) so a markdown cell can be
    correlated with the JSON-tree TableCell that produced it.

    This is the bug Akhilesh hit on the post-PR-#36 run: the
    crop injector was previously using
    ``_clean_for_signature_check`` (which strips dates) as the
    match key. For L4 cells whose only content is a date, the
    cleaned key was empty → no match → no crop. 0 sigcrop files
    written for the whole 112-page doc.

    The two functions serve DIFFERENT purposes:
      * ``_clean_for_signature_check`` — "is there non-date
        content worth treating as a signature?" Strips dates.
      * ``_normalize_for_match`` — "what's the cell's identity
        for cross-referencing?" Keeps everything except markup
        and the ``[Signature]`` marker.
    """
    from app.adapters.ocr.signature_enricher import _normalize_for_match

    # Date-only cell — keep the date intact.
    assert _normalize_for_match("[Signature] 23/11/2025") == "23/11/2025"
    # Block-level tags (``<br>``) collapse to space, inline tags
    # (``<i>``) collapse to nothing → produces tokens separated
    # by a single space.
    assert _normalize_for_match("[Signature] <i>FE</i><br>22/11/2025") == "FE 22/11/2025"
    assert _normalize_for_match("[Signature] N089<br>25/11/2025") == "N089 25/11/2025"
    # Already-marker cell — empty key (legitimately no content).
    assert _normalize_for_match("[Signature]") == ""
    # Whitespace collapsing.
    assert _normalize_for_match("  [Signature]   AK\t\t\n03/10/2025  ") == "AK 03/10/2025"
    # The load-bearing property: identical content from either
    # side (markdown with tags, JSON with HTML stripped) must
    # produce the same normalized key so the bbox correlator
    # finds the match.
    md_cell = "[Signature] <i>N089</i><br>22/11/2025"
    json_cell = "N089 22/11/2025"  # what _extract_bboxes_from_json typically gives
    assert _normalize_for_match(md_cell) == _normalize_for_match(json_cell)
    # And a JSON cell with a newline at the <br> position
    # (Datalab's other emission style) normalizes identically.
    json_cell_with_nl = "N089\n22/11/2025"
    assert _normalize_for_match(md_cell) == _normalize_for_match(json_cell_with_nl)


def test_image_placeholder_stripped_on_new_cell() -> None:
    """Datalab emits ``<p>Image: signature</p>`` placeholder text
    when image extraction is disabled. The placeholder clutters
    the side-pane:
        [Signature]
        Image:
        signature
        26/11/2025

    Stack-rendered. The L_IMG_PLACEHOLDER layer fires, strips
    the literal, and injects ``[Signature]`` cleanly."""
    md = """\
| Step | Done by | Checked by |
|------|---------|------------|
| 1 | <p>Image: signature</p> 26/11/2025 | <p>Image: signature</p> 26/11/2025 |
"""
    result = enrich_page(md, [], page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)
    assert "[Signature]" in result.markdown
    assert "Image: signature" not in result.markdown
    assert "<p>" not in result.markdown.replace("[Signature]", "")
    assert result.telemetry.layer_counts["L_IMG_PLACEHOLDER"] == 2


def test_image_placeholder_stripped_on_already_marked_cell() -> None:
    """Idempotency edge case: when a cell ALREADY has
    ``[Signature]`` from a prior run AND still has the
    ``<p>Image: signature</p>`` placeholder, the strip must
    still happen so reviewers see a clean cell. Skipping
    cleanup on idempotent cells leaves the noise in place
    forever (the symptom Akhilesh reported)."""
    md = """\
| Step | Done by |
|------|---------|
| 1 | [Signature] <p>Image: signature</p> 26/11/2025 |
"""
    result = enrich_page(md, [], page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)
    assert "Image: signature" not in result.markdown
    # Marker preserved (not re-injected).
    assert result.markdown.count("[Signature]") == 1
    # The strip on an already-enriched cell is attributed to
    # L_IMG_PLACEHOLDER (not skipped_idempotent) so re-runs over
    # already-enriched docs surface cleanup work in telemetry.
    assert result.telemetry.layer_counts["L_IMG_PLACEHOLDER"] == 1
    assert result.telemetry.skipped_idempotent == 0


def test_date_only_predicate() -> None:
    """``_is_date_only_or_empty`` returns True ONLY when the
    cell contains a date AND nothing else of substance.
    Tightened on 2026-05-12 — filler-only cells (whitespace,
    dashes) now route through ``_is_filler_only`` instead, so
    L4 doesn't stamp ``[Signature] ----`` false positives.
    """
    # Date-only cases (date + filler):
    assert _is_date_only_or_empty("03/10/2025")
    assert _is_date_only_or_empty(" 03-10-2025 ")
    assert _is_date_only_or_empty("3/10/25")
    assert _is_date_only_or_empty("<br>27/11/2025")
    assert _is_date_only_or_empty("27/11/2025 -")
    # Not date-only (no date present, or extra text):
    assert not _is_date_only_or_empty("")
    assert not _is_date_only_or_empty(" ")
    assert not _is_date_only_or_empty("---")
    assert not _is_date_only_or_empty("[Signature]")
    assert not _is_date_only_or_empty("AK 03/10/2025")
    assert not _is_date_only_or_empty("OK")


def test_signature_column_header_detection() -> None:
    patterns = ("done by", "checked by", "operator")
    assert _is_signature_column_header("Done by", patterns)
    assert _is_signature_column_header("  DONE   BY  ", patterns)
    assert _is_signature_column_header("Operator Signature", patterns)
    assert not _is_signature_column_header("Net Qty", patterns)
    assert not _is_signature_column_header("", patterns)


# ── Bbox containment helper ──────────────────────────────────


def test_bbox_contains_center_inside() -> None:
    outer = JsonBlock(block_type="TableCell", polygon=_poly(0, 0, 100, 100), page_num=1)
    inner = JsonBlock(block_type="Signature", polygon=_poly(40, 40, 60, 60), page_num=1)
    assert _bbox_contains(outer, inner)


def test_bbox_contains_center_outside() -> None:
    outer = JsonBlock(block_type="TableCell", polygon=_poly(0, 0, 100, 100), page_num=1)
    inner = JsonBlock(block_type="Signature", polygon=_poly(200, 200, 300, 300), page_num=1)
    assert not _bbox_contains(outer, inner)


def test_bbox_contains_different_pages() -> None:
    outer = JsonBlock(block_type="TableCell", polygon=_poly(0, 0, 100, 100), page_num=1)
    inner = JsonBlock(block_type="Signature", polygon=_poly(40, 40, 60, 60), page_num=2)
    assert not _bbox_contains(outer, inner)


# ── Table enumeration ────────────────────────────────────────


def test_enumerate_tables_finds_each_table() -> None:
    md = """\
# Heading

Some paragraph text.

| A | B |
|---|---|
| 1 | 2 |

More text.

| C | D |
|---|---|
| 3 | 4 |

Tail.
"""
    tables = _enumerate_tables(md)
    assert len(tables) == 2


def test_enumerate_tables_ignores_pseudo_tables_without_separator() -> None:
    md = """\
| Not a real table |
| Just pipes |
"""
    tables = _enumerate_tables(md)
    assert len(tables) == 0


# ── End-to-end realistic BPCR row ───────────────────────────


def test_l4_priority_lowest_l2_still_wins() -> None:
    """When all three layers' triggers are present, L2 wins."""
    md = """\
| Step | Done by |
|------|---------|
| 1 | 03/10/2025 |
"""
    blocks = [_signature_block(page_num=3), _handwriting_block(page_num=3)]
    result = enrich_page(md, blocks, page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)

    assert result.telemetry.layer_counts["L2"] == 1
    assert result.telemetry.layer_counts["L3"] == 0
    assert result.telemetry.layer_counts["L4"] == 0


def test_l4_does_not_synthesize_in_empty_cells() -> None:
    """Even with aggressive=True (L4 default), empty cells in
    signature columns are NEVER stamped. The load-bearing
    invariant is preserved across all four layers."""
    md = """\
| Step | Done by |
|------|---------|
| 1 |  |
"""
    result = enrich_page(md, [], page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)
    assert "[Signature]" not in result.markdown
    assert result.telemetry.injected_count == 0


def test_l_img_injects_alongside_img_tag_in_signature_column() -> None:
    """L_IMG path: cell has ``<img data-bbox="...">`` Datalab tag in
    a signature-named column → inject ``[Signature]`` text BEFORE
    the img tag. Frontend then renders both: text marker for
    grep/rule-5, image for HITL visual review.

    This is the path that fixes Akhilesh's UIIBEHSII28 dispensing
    table — page 3 alone had 32 such tags previously skipped
    because the cells weren't 'date-only-or-empty'."""
    md = """\
| Step | Done by | Checked by |
|------|---------|------------|
| 1 | <img data-bbox="1268 582 1356 660" src="aaa_img.jpg"/> | <img data-bbox="1372 560 1532 660" src="bbb_img.jpg"/> |
"""
    result = enrich_page(md, [], page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)
    assert "[Signature]" in result.markdown
    # Both the text marker AND the img tag must coexist.
    assert "<img" in result.markdown, "img tag must NOT be removed — frontend renders it"
    assert result.telemetry.layer_counts["L_IMG"] == 2
    assert result.telemetry.injected_count == 2


def test_l_img_fires_even_in_strict_mode_aggressive_false() -> None:
    """L_IMG is deterministic (Datalab cropped the region). It
    must fire even when aggressive=False so strict-classifier
    callers still get image-region signatures."""
    md = """\
| Step | Done by |
|------|---------|
| 1 | <img data-bbox="0 0 10 10" src="aaa_img.jpg"/> |
"""
    result = enrich_page(
        md, [], page_num=3,
        signature_column_headers=_SIGNATURE_COLUMNS, aggressive=False,
    )
    assert result.telemetry.layer_counts["L_IMG"] == 1
    assert "[Signature]" in result.markdown


def test_l_img_does_not_fire_on_img_outside_signature_column() -> None:
    """A Datalab img tag in a NON-signature column (e.g.
    figure / logo / chart) must NOT trigger signature
    injection. Otherwise every page with a header logo
    would be flagged as signed."""
    md = """\
| Step | Diagram | Done by |
|------|---------|---------|
| 1 | <img data-bbox="0 0 100 100" src="xxx_img.jpg"/> | 03/10/2025 |
"""
    result = enrich_page(md, [], page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)
    # No L_IMG injection — the img is in 'Diagram', not 'Done by'.
    assert result.telemetry.layer_counts["L_IMG"] == 0
    # But L4 still fires on the date-only Done by cell.
    assert result.telemetry.layer_counts["L4"] == 1


def test_l_hwtext_italic_handwriting_in_signature_column_injects() -> None:
    """L_HWTEXT path: Datalab transcribed handwritten initials as
    italic-wrapped text (``<i>FE</i>``). Inject ``[Signature]``
    alongside so frontends still mark the cell as signed.

    This is the gap on 2538105061.pdf pages 6/18 where Datalab
    chose to OCR the initials instead of cropping them — 44
    cells across the doc had this shape pre-fix."""
    md = """\
| Step | Done By | Checked By |
|------|---------|------------|
| 1 | <i>N089</i><br>22/11/2025 | <i>FE</i><br>22/11/2025 |
"""
    result = enrich_page(md, [], page_num=6, signature_column_headers=_SIGNATURE_COLUMNS)
    assert "[Signature]" in result.markdown
    assert result.markdown.count("[Signature]") == 2
    assert result.telemetry.layer_counts["L_HWTEXT"] == 2
    # Original italic content preserved — operator can still read
    # what Datalab transcribed.
    assert "<i>N089</i>" in result.markdown
    assert "<i>FE</i>" in result.markdown


def test_l_text_short_non_date_text_in_signature_column_injects() -> None:
    """L_TEXT path: short text content (initials Datalab OCR'd
    without italic markup) in a signature column → inject.
    The user's stated rule: 'anything except date comes in
    they should be identified as signature'."""
    md = """\
| Step | Done By | Checked By |
|------|---------|------------|
| 1 | N089<br>25/11/2025 | ALE<br>25/11/2025 |
"""
    result = enrich_page(md, [], page_num=12, signature_column_headers=_SIGNATURE_COLUMNS)
    assert result.markdown.count("[Signature]") == 2
    assert result.telemetry.layer_counts["L_TEXT"] == 2


def test_l_text_skips_verdict_words_no_false_positives() -> None:
    """A Done-by cell with 'OK' / 'PASS' / 'NA' is an operator
    verdict, not a signature. The verdict-skip list must
    prevent these from being stamped as signed."""
    md = """\
| Step | Done By | Checked By |
|------|---------|------------|
| 1 | OK | PASS |
| 2 | N/A | approved |
"""
    result = enrich_page(md, [], page_num=1, signature_column_headers=_SIGNATURE_COLUMNS)
    assert "[Signature]" not in result.markdown, (
        "verdict words like OK/PASS/NA must NEVER be stamped as signatures"
    )
    assert result.telemetry.layer_counts["L_HWTEXT"] == 0
    assert result.telemetry.layer_counts["L_TEXT"] == 0
    assert result.telemetry.skipped_verdict == 4


def test_l_text_skips_long_prose_no_false_positives() -> None:
    """A Done-by cell containing a long descriptive note is
    legitimate documentation, not a signature. The
    MAX_SIG_TEXT_CHARS=40 cap must prevent injection.

    If real BPCRs surface legitimate initials beyond 40 chars
    we'll see ``skipped_long_text`` in telemetry and can revisit
    the threshold."""
    md = """\
| Step | Done By |
|------|---------|
| 1 | Initial sampling completed by quality team on time as scheduled |
"""
    result = enrich_page(md, [], page_num=1, signature_column_headers=_SIGNATURE_COLUMNS)
    assert "[Signature]" not in result.markdown
    assert result.telemetry.skipped_long_text == 1


def test_l_hwtext_outranks_l_text_when_italic_present() -> None:
    """When a cell has italic markup, L_HWTEXT must win even
    if L_TEXT could also fire. Italic is a stronger signal
    (Datalab actively marked it as handwriting) — confidence
    0.55 vs 0.40."""
    md = """\
| Step | Done By |
|------|---------|
| 1 | <i>AK</i> |
"""
    result = enrich_page(md, [], page_num=1, signature_column_headers=_SIGNATURE_COLUMNS)
    assert result.telemetry.layer_counts["L_HWTEXT"] == 1
    assert result.telemetry.layer_counts["L_TEXT"] == 0


def test_dash_only_cell_never_stamped_filler_check() -> None:
    """The false positive Akhilesh reported on 2538105061.pdf:
    ``[Signature] ----`` and ``[Signature] —`` were appearing on
    cells whose entire content was filler dashes. Those are
    legitimate ``no data captured`` cells — a missing-signature
    finding rule 5 will flag. Must never be stamped.

    Tightened on 2026-05-12 via :func:`_is_filler_only`."""
    md = """\
| Step | Done By | Checked By |
|------|---------|------------|
| 1 | ---- | --- |
| 2 | — | —— |
| 3 | __ | __ |
"""
    result = enrich_page(md, [], page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)
    assert "[Signature]" not in result.markdown
    assert result.telemetry.injected_count == 0


def test_br_prefix_date_cell_recognized_as_date_only() -> None:
    """Page 20 of 2538105061.pdf had cells like ``<br>27/11/2025``
    that the L4 path was skipping because ``<br>`` survived the
    strip. Fixed by tag-stripping inside ``_is_date_only_or_empty``.

    The underlying cell IS a date-only cell (a row that was
    signed-and-dated where OCR captured only the date) — L4
    should fire."""
    md = """\
| Step | Done By | Checked By |
|------|---------|------------|
| 1 | <br>27/11/2025 | <br>27/11/2025 |
"""
    result = enrich_page(md, [], page_num=20, signature_column_headers=_SIGNATURE_COLUMNS)
    assert "[Signature]" in result.markdown
    assert result.telemetry.layer_counts["L4"] == 2


def test_underscore_filler_treated_as_empty() -> None:
    """Some templates use underscores instead of dashes as filler
    (``___`` / ``____``). The filler predicate covers these too."""
    md = """\
| Step | Done By |
|------|---------|
| 1 | ___ |
| 2 | ____ |
"""
    result = enrich_page(md, [], page_num=1, signature_column_headers=_SIGNATURE_COLUMNS)
    assert "[Signature]" not in result.markdown


def test_empty_cell_still_never_stamped_with_new_layers() -> None:
    """The load-bearing invariant survives the new layers:
    an empty signature-column cell is a legitimate
    missing-signature finding, never papered over."""
    md = """\
| Step | Done By | Checked By |
|------|---------|------------|
| 1 |   |  |
| 2 |  | ALE |
"""
    result = enrich_page(md, [], page_num=1, signature_column_headers=_SIGNATURE_COLUMNS)
    # Row 1 cells empty → no inject. Row 2: Done-by empty, Checked-by has text.
    assert result.markdown.count("[Signature]") == 1
    # The single inject is in Checked By only.
    assert result.telemetry.layer_counts["L_TEXT"] == 1


def test_l_img_outranks_l4_when_both_could_fire() -> None:
    """A cell with both an img tag AND a date is a strong
    L_IMG signal (Datalab cropped it) — must use L_IMG
    confidence, not L4."""
    md = """\
| Step | Done by |
|------|---------|
| 1 | <img data-bbox="0 0 10 10" src="aaa.jpg"/> 03/10/2025 |
"""
    result = enrich_page(md, [], page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)
    assert result.telemetry.layer_counts["L_IMG"] == 1
    assert result.telemetry.layer_counts["L4"] == 0


def test_end_to_end_realistic_bpcr_dispensing_row() -> None:
    """A BPCR raw-material dispensing table — the exact shape
    Akhilesh's screenshot showed. Datalab classified the
    signatures as Handwriting only; the enricher must restore
    ``[Signature]`` markers."""
    md = """\
| S. No. | Raw Material | Standard Qty | Net Qty | Done by | Checked by |
|--------|--------------|--------------|---------|---------|------------|
| 1 | Ethyl acetate | 6396 L | 2000 | 23/11/2025 | 23/11/2025 |
| 1 | Ethyl acetate | 6396 L | 1200 | 24/11/2025 | 24/11/2025 |
| 2 | Sertraline | 600 Kg | 53.212 | 23/11/2025 | 23/11/2025 |
"""
    blocks = [_handwriting_block(page_num=3) for _ in range(6)]
    result = enrich_page(md, blocks, page_num=3, signature_column_headers=_SIGNATURE_COLUMNS)

    sig_count = len(EXISTING_MARKER_RE.findall(result.markdown))
    assert sig_count == 6  # 3 rows × 2 signature columns
    assert result.telemetry.layer_counts["L3"] == 6
    assert result.telemetry.signature_columns_detected == 2
