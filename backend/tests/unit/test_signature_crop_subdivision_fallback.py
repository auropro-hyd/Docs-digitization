"""Pin the row-bbox subdivision fallback for signature crops.

Background: the signature crop pipeline (``_inject_signature_crops``)
needs ``TableCell`` blocks with per-cell polygons from Datalab's
JSON tree to crop each cell as a JPG and inject ``<img>`` tags.
Datalab's classifier is non-deterministic per doc — some runs emit
no ``TableCell`` blocks at all even when ``mode=accurate`` is set
(the e5e35ffc-… symptom on 2026-05-12: ``total_table_cells_in_bbox
_data: 0`` on every chunk). Without TableCells the crop pipeline
historically bailed loudly to telemetry but produced no crops, so
``[Signature]`` markers landed in markdown with no visual evidence.

This module pins the architectural fallback: when ``TableCell``
blocks are absent BUT ``Table`` polygons are present, the adapter
synthesises TableCell entries by uniformly subdividing each Table
polygon by the row/column count inferred from the markdown table.
The synthesised entries flow through the same text-match → crop →
inject pipeline as real Datalab TableCells.
"""

from __future__ import annotations

import pytest

from app.adapters.ocr.datalab import _synthesize_table_cells_from_polygons
from app.core.ports.ocr import OCRPageResult


def _square_polygon(x: float, y: float, w: float, h: float) -> list[list[float]]:
    """Helper: build a 4-corner polygon clockwise from top-left."""
    return [
        [x, y],
        [x + w, y],
        [x + w, y + h],
        [x, y + h],
    ]


# ── Happy path ─────────────────────────────────────────────────


def test_synthesises_table_cells_for_signature_columns_only() -> None:
    """The fallback only emits synthetic cells for cells in
    signature-column rows that contain ``[Signature]`` markers.
    Non-signature columns and non-signature rows are skipped —
    we don't crop every cell of every table, only the ones the
    enricher would actually inject."""

    markdown = (
        "# Page 4\n\n"
        "| Step | Description | Done by | Date |\n"
        "|------|-------------|---------|------|\n"
        "| 1    | Weigh API    | [Signature] | 26/11/2025 |\n"
        "| 2    | Add solvent  | [Signature] | 26/11/2025 |\n"
        "| 3    | Stir 30 min  | --          | 26/11/2025 |\n"
    )
    page = OCRPageResult(page_num=4, markdown=markdown)
    # Datalab gave a Table polygon but NO TableCells (the symptom).
    bbox_data = {
        4: [
            ("Table", "", _square_polygon(100, 200, 400, 200)),
        ],
    }

    out = _synthesize_table_cells_from_polygons(
        [page], bbox_data, sig_columns=("done by", "checked by"),
    )

    assert 4 in out
    # Two rows with [Signature] in the "Done by" column → 2 synthetic
    # cells. Row 3 ("--") is filler and must NOT be synthesised
    # because EXISTING_MARKER_RE doesn't match a bare dash.
    cells_p4 = out[4]
    assert len(cells_p4) == 2, (
        f"expected 2 synthetic [Signature] cells, got {len(cells_p4)}"
    )
    # Both must be flagged as TableCell so the rest of the pipeline
    # treats them identically to Datalab-emitted cells.
    assert all(bt == "TableCell" for (bt, _t, _poly) in cells_p4)
    # Text content matches the markdown cell so the existing text-
    # match path in _inject_signature_crops will pair them.
    cell_texts = [txt for (_bt, txt, _poly) in cells_p4]
    assert all("[Signature]" in t for t in cell_texts)


def test_polygon_subdivision_is_uniform_by_grid_dims() -> None:
    """Cell bboxes are computed by uniform subdivision: a 4-column
    table's signature cell in row 1 (data row, after header) at
    column 2 (zero-indexed) should land at the right slice of the
    Table polygon. Uniform assumption is intentional (acceptable
    first-pass; tighter alignment needs Datalab TableCells)."""

    # 4 columns, 1 data row + header = 2 grid rows.
    # Table polygon: (x=100, y=0) to (x=500, y=200), so:
    #   col_w = (500-100) / 4 = 100
    #   row_h = (200-0) / 2 = 100
    # Signature column = index 2 (header "Done by").
    # Data row 1 → grid row 1 (header is row 0).
    # Expected cell bbox: x ∈ [300, 400], y ∈ [100, 200].
    markdown = (
        "| Step | Date | Done by | Notes |\n"
        "|------|------|---------|-------|\n"
        "| 1    | 26/11 | [Signature] | OK |\n"
    )
    page = OCRPageResult(page_num=1, markdown=markdown)
    bbox_data = {1: [("Table", "", _square_polygon(100, 0, 400, 200))]}

    out = _synthesize_table_cells_from_polygons(
        [page], bbox_data, sig_columns=("done by",),
    )

    cells = out.get(1, [])
    assert len(cells) == 1
    _bt, _txt, poly = cells[0]
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    # 4-column subdivision: signature column at index 2 → x slice [300, 400].
    assert min(xs) == pytest.approx(300.0), f"x_min: {min(xs)}"
    assert max(xs) == pytest.approx(400.0), f"x_max: {max(xs)}"
    # 2-row subdivision (header + 1 data row) → data row 1 in slice [100, 200].
    assert min(ys) == pytest.approx(100.0), f"y_min: {min(ys)}"
    assert max(ys) == pytest.approx(200.0), f"y_max: {max(ys)}"


# ── Negative paths ─────────────────────────────────────────────


def test_no_synthesis_when_no_table_polygons() -> None:
    """If Datalab returned neither TableCells nor Tables, the
    fallback has nothing to subdivide and must return empty —
    NOT raise, NOT synthesise zero-area cells from nothing."""

    markdown = "| Step | Done by |\n|------|---------|\n| 1 | [Signature] |\n"
    page = OCRPageResult(page_num=1, markdown=markdown)
    bbox_data: dict = {1: [("Text", "header", _square_polygon(0, 0, 100, 50))]}

    out = _synthesize_table_cells_from_polygons(
        [page], bbox_data, sig_columns=("done by",),
    )
    assert out == {}


def test_no_synthesis_when_no_signature_marker_in_markdown() -> None:
    """A table that has no ``[Signature]`` markers anywhere must
    not produce synthetic cells. The fallback is targeted at the
    crop pipeline's actual workload."""

    markdown = (
        "| Step | Done by |\n|------|---------|\n| 1 | initials AK |\n"
    )
    page = OCRPageResult(page_num=1, markdown=markdown)
    bbox_data = {1: [("Table", "", _square_polygon(0, 0, 200, 100))]}

    out = _synthesize_table_cells_from_polygons(
        [page], bbox_data, sig_columns=("done by",),
    )
    assert out == {}


def test_no_synthesis_when_signature_column_header_missing() -> None:
    """If the markdown table's header has no signature-named
    column, the fallback skips that table entirely — even if a
    [Signature] marker happens to appear somewhere."""

    markdown = (
        "| Step | Status |\n|------|--------|\n| 1 | [Signature] |\n"
    )
    page = OCRPageResult(page_num=1, markdown=markdown)
    bbox_data = {1: [("Table", "", _square_polygon(0, 0, 200, 100))]}

    out = _synthesize_table_cells_from_polygons(
        [page], bbox_data, sig_columns=("done by",),
    )
    assert out == {}


def test_no_synthesis_for_dash_only_cells() -> None:
    """Filler cells (just dashes / —) must NOT be synthesised
    even when they're in a signature column. The signature
    enricher would never crop them anyway."""

    markdown = (
        "| Step | Done by |\n|------|---------|\n"
        "| 1 | --  |\n"
        "| 2 | --- |\n"
        "| 3 | [Signature] |\n"
    )
    page = OCRPageResult(page_num=1, markdown=markdown)
    bbox_data = {1: [("Table", "", _square_polygon(0, 0, 200, 200))]}

    out = _synthesize_table_cells_from_polygons(
        [page], bbox_data, sig_columns=("done by",),
    )
    cells = out.get(1, [])
    assert len(cells) == 1, (
        f"expected only the row 3 [Signature] cell to be synthesised, "
        f"got {len(cells)}"
    )


def test_multiple_tables_paired_by_reading_order() -> None:
    """Two tables on a page get two distinct Table polygons. The
    fallback must pair them positionally — the first markdown
    table maps to the first polygon. Mis-pairing would put cells
    in the wrong PDF region."""

    markdown = (
        "| Step | Done by |\n|------|---------|\n"
        "| 1 | [Signature] |\n\n"
        "Some prose between tables.\n\n"
        "| Item | Done by |\n|------|---------|\n"
        "| A | [Signature] |\n"
    )
    page = OCRPageResult(page_num=1, markdown=markdown)
    bbox_data = {
        1: [
            ("Table", "", _square_polygon(0, 0, 100, 100)),    # top table
            ("Table", "", _square_polygon(0, 500, 100, 100)),  # bottom table
        ],
    }

    out = _synthesize_table_cells_from_polygons(
        [page], bbox_data, sig_columns=("done by",),
    )
    cells = out.get(1, [])
    assert len(cells) == 2
    # Sort by Y to verify each cell landed in its source table's region.
    cells_by_y = sorted(cells, key=lambda c: min(p[1] for p in c[2]))
    top_y = min(p[1] for p in cells_by_y[0][2])
    bot_y = min(p[1] for p in cells_by_y[1][2])
    assert top_y < 100, "first markdown table → first polygon (top region)"
    assert bot_y >= 500, "second markdown table → second polygon (bottom region)"


def test_degenerate_polygon_skipped() -> None:
    """A zero-width or zero-height polygon would produce nonsense
    bboxes. The fallback must skip such Tables rather than emit
    cells with negative dimensions."""

    markdown = (
        "| Step | Done by |\n|------|---------|\n| 1 | [Signature] |\n"
    )
    page = OCRPageResult(page_num=1, markdown=markdown)
    # Zero-height polygon — all corners on the same y.
    degenerate = [[100, 50], [200, 50], [200, 50], [100, 50]]
    bbox_data = {1: [("Table", "", degenerate)]}

    out = _synthesize_table_cells_from_polygons(
        [page], bbox_data, sig_columns=("done by",),
    )
    assert out == {}
