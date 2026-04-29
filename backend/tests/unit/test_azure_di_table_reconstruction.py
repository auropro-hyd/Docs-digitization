from types import SimpleNamespace

from app.adapters.ocr.azure_di import _build_table_html, _extract_page_markdown


def _cell(row: int, col: int, text: str, *, row_span: int = 1, col_span: int = 1, kind: str = "content"):
    return SimpleNamespace(
        row_index=row,
        column_index=col,
        row_span=row_span,
        column_span=col_span,
        content=text,
        kind=kind,
        bounding_regions=[SimpleNamespace(page_number=1)],
    )


def test_build_table_html_clamps_overflowing_colspan():
    table = SimpleNamespace(
        column_count=3,
        cells=[
            _cell(0, 0, "A"),
            _cell(0, 1, "B"),
            _cell(0, 2, "C", col_span=5),
        ],
        caption=None,
        footnotes=[],
    )

    html = _build_table_html(table, page_num=1)
    assert "<table>" in html
    assert "C</td>" in html
    assert 'colspan="5"' not in html


def test_extract_page_markdown_replaces_tables_by_table_index():
    # Two table ranges with non-contiguous source indices (0 and 2 — index
    # 1 is intentionally absent) prove the replacement looks up
    # ``page_tables`` by ``tbl_idx`` rather than by positional order.
    # The bug this guards against was the pre-fix code that walked
    # ``page_tables`` as a list and gave the second range whichever HTML
    # happened to be at list[1] — which here would be table index 2's
    # content for table index 0 (or worse, an IndexError).
    content = "__00__--__22__"
    az_page = SimpleNamespace(spans=[SimpleNamespace(offset=0, length=len(content))])
    table_ranges = [(2, 6, 0), (10, 14, 2)]
    page_tables = {
        2: "<table>T2</table>",
        0: "<table>T0</table>",
    }

    page_md = _extract_page_markdown(
        content=content,
        az_page=az_page,
        page_tables=page_tables,
        table_ranges=table_ranges,
    )

    assert page_md is not None
    # ``[2:6]`` ("00__") is replaced by table 0; the leading "__" survives,
    # the original "--" separator stays where it was.
    assert "__<table>T0</table>--" in page_md
    # ``[10:14]`` ("22__") is replaced by table 2; the leading "__" survives.
    # The trailing edge of the slice is the end of ``content`` so no
    # trailing characters to assert on.
    assert "--__<table>T2</table>" in page_md
    # Neither table's HTML was swapped with the other (i.e. the tbl_idx
    # → page_tables mapping is honoured for both ranges).
    assert page_md.count("<table>T0</table>") == 1
    assert page_md.count("<table>T2</table>") == 1
