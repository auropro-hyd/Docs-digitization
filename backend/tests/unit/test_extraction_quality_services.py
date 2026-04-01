from app.core.services.layout_markdown_sanitizer import sanitize_layout_markdown
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
