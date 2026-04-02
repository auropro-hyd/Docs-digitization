from app.core.services.layout_markdown_sanitizer import classify_parser_repair_severity, sanitize_layout_markdown
from app.core.services.selection_semantics import summarize_selection_semantics


def test_sanitize_layout_markdown_repairs_known_fragments():
    src = """
-- PageBreak -->
<<table>
</t<table>
table>abl<table>
</table></td></tr></table>
<!
"""
    out, repairs = sanitize_layout_markdown(src)
    assert "<!-- PageBreak -->" in out
    assert "<<table>" not in out
    assert "</t<table>" not in out
    assert "table>abl<table>" not in out
    assert "</table></td></tr></table>" not in out
    assert out.strip().endswith("</table>")
    assert "normalized_pagebreak_markers" in repairs
    assert "fixed_repeated_table_open" in repairs


def test_sanitize_layout_markdown_repairs_broken_table_open_and_pagebreak_tail():
    src = """
eBreak -->
</<table>
tr>
th>Header</th>
td>Value</td>
"""
    out, repairs = sanitize_layout_markdown(src)
    assert "eBreak -->" not in out
    assert "</<table>" not in out
    assert "<table>" in out
    assert "\n<tr>" in out
    assert "\n<th>Header</th>" in out
    assert "\n<td>Value</td>" in out
    assert "fixed_broken_table_open" in repairs
    assert "removed_broken_pagebreak_tail" in repairs
    assert "fixed_missing_angle_table_tag" in repairs


def test_sanitize_layout_markdown_repairs_stranded_table_close_tokens():
    src = """
<table>
<tr>
<td>Value</td>
/tr>
/tbody>
/table
"""
    out, repairs = sanitize_layout_markdown(src)
    assert "\n/tr>" not in out
    assert "\n/tbody>" not in out
    assert "\n/table" not in out
    assert "\n</tr>" in out
    assert "\n</tbody>" in out
    assert "\n</table>" in out
    assert "fixed_missing_angle_close_table_tag" in repairs or "fixed_stranded_table_close_token" in repairs


def test_sanitize_layout_markdown_repairs_broken_pagenumber_and_join():
    src = """
<!-- PageNumber="Page 19 <table>
<tbody>
<tr><td>A</td></tr>
</table>/tr<table>
<tr><td>B</td></tr>
</table>
"""
    out, repairs = sanitize_layout_markdown(src)
    assert '<!-- PageNumber="' not in out
    assert "/tr<table>" not in out
    assert "<table>" in out
    assert "</tr>\n<table>" in out
    assert "removed_broken_pagenumber_comment" in repairs
    assert "fixed_broken_table_join_no_angle_close" in repairs


def test_selection_semantics_detects_ambiguous_selected_without_headers():
    markdown = "Checklist block without explicit response headers"
    marks = [{"state": "selected", "bounding_region": None}]
    summary = summarize_selection_semantics(markdown, marks)
    assert summary["has_selection_marks"] is True
    assert summary["selected_count"] == 1
    assert summary["ambiguous"] is True
    assert "selected_without_checklist_headers" in summary["ambiguity_reasons"]


def test_selection_semantics_detects_tri_state_headers():
    markdown = "S.No Checkpoints YES NO NA"
    marks = [
        {"state": "selected", "bounding_region": {"x": 1, "y": 1, "width": 1, "height": 1}},
        {"state": "unselected", "bounding_region": {"x": 2, "y": 1, "width": 1, "height": 1}},
    ]
    summary = summarize_selection_semantics(markdown, marks)
    assert summary["checklist_headers"]["tri_state"] is True
    assert summary["unknown_count"] == 0


def test_parser_repair_severity_scoring():
    sev, score = classify_parser_repair_severity(
        ["fixed_fragment_t_table", "removed_table_td_suffix", "normalized_pagebreak_markers"]
    )
    assert sev in {"medium", "high"}
    assert score > 0
