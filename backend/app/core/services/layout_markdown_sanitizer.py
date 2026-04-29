"""Deterministic cleanup for malformed layout-markdown fragments."""

from __future__ import annotations

import re

_PAGEBREAK_VARIANT_RE = re.compile(r"(?:<!--\s*)?-{0,2}\s*PageBreak\s*-->\s*", re.IGNORECASE)
_TRUNCATED_COMMENT_RE = re.compile(r"(?:<!\s*|<!--\s*)$")
_ORPHAN_TABLE_WRAPPER_RE = re.compile(r"</table>\s*</td>\s*</tr>\s*</table>", re.IGNORECASE)
_REPEATED_TABLE_OPEN_RE = re.compile(r"<{2,}\s*table>", re.IGNORECASE)
_BROKEN_TABLE_JOIN_RE = re.compile(r"</t\s*<table>", re.IGNORECASE)
_BROKEN_TABLE_OPEN_RE = re.compile(r"</<\s*table\s*>", re.IGNORECASE)
_BROKEN_PAGEBREAK_TAIL_RE = re.compile(
    r"(?:^|\n)\s*(?:[a-z]*break|eak|reak|agebreak)\s*-->\s*",
    re.IGNORECASE,
)
# Trailing match is ``[ \t]*`` (not ``\s*``) on purpose — ``\s`` would
# eat the line-terminating ``\n`` and glue the next tag onto the same
# line (e.g. ``tr>\n<th>...`` → ``<tr><th>...``). The fixer should
# repair the malformed tag in place; line topology must survive.
_BROKEN_TABLE_ROW_OPEN_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)(tr|td|th|thead|tbody|tfoot|table)>[ \t]*"
)
_BROKEN_TABLE_ROW_CLOSE_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)/(tr|td|th|thead|tbody|tfoot|table)>[ \t]*"
)
_BROKEN_TABLE_ROW_CLOSE_NO_BRACKET_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)/(tr|td|th|thead|tbody|tfoot|table)[ \t]*$"
)
_BROKEN_PAGENUM_WITH_TABLE_RE = re.compile(r'<!--\s*PageNumber="[^"\n]*<table>', re.IGNORECASE)
_BROKEN_TABLE_JOIN_NO_ANGLE_CLOSE_RE = re.compile(r"(?i)/(tr|td|th|thead|tbody|tfoot)\s*<table>")

_SEVERE_REPAIRS = {
    "fixed_fragment_t_table",
    "fixed_fragment_abl_table",
    "removed_orphan_table_wrapper",
    "fixed_broken_table_join",
    "fixed_broken_table_open",
}

_MEDIUM_REPAIRS = {
    "removed_table_td_suffix",
    "fixed_repeated_table_open",
    "removed_truncated_comment_tail",
    "removed_broken_pagebreak_tail",
    "fixed_missing_angle_table_tag",
    "fixed_missing_angle_close_table_tag",
    "fixed_stranded_table_close_token",
    "removed_broken_pagenumber_comment",
    "fixed_broken_table_join_no_angle_close",
}


def sanitize_layout_markdown(md: str) -> tuple[str, list[str]]:
    """Normalize recurrent malformed HTML/markdown fragments from OCR output.

    Returns:
        A tuple of (cleaned_markdown, repair_tags).
    """
    if not md:
        return md, []

    repairs: list[str] = []
    out = md

    new = _PAGEBREAK_VARIANT_RE.sub("<!-- PageBreak -->\n\n", out)
    if new != out:
        repairs.append("normalized_pagebreak_markers")
        out = new

    new = _REPEATED_TABLE_OPEN_RE.sub("<table>", out)
    if new != out:
        repairs.append("fixed_repeated_table_open")
        out = new

    new = _BROKEN_TABLE_JOIN_RE.sub("</table>\n<table>", out)
    if new != out:
        repairs.append("fixed_broken_table_join")
        out = new

    # OCR occasionally corrupts "<table>" into "</<table>".
    new = _BROKEN_TABLE_OPEN_RE.sub("<table>", out)
    if new != out:
        repairs.append("fixed_broken_table_open")
        out = new

    new = _ORPHAN_TABLE_WRAPPER_RE.sub("</table>", out)
    if new != out:
        repairs.append("removed_orphan_table_wrapper")
        out = new

    # Repair common injected fragments found at table boundaries.
    for needle, replacement, tag in (
        ("</t<table>", "</table>\n<table>", "fixed_fragment_t_table"),
        ("table>abl<table>", "table>\n<table>", "fixed_fragment_abl_table"),
        ("</table>td>", "</table>", "removed_table_td_suffix"),
    ):
        new = out.replace(needle, replacement)
        if new != out:
            repairs.append(tag)
            out = new

    new = _TRUNCATED_COMMENT_RE.sub("", out)
    if new != out:
        repairs.append("removed_truncated_comment_tail")
        out = new

    new = _BROKEN_PAGEBREAK_TAIL_RE.sub("\n", out)
    if new != out:
        repairs.append("removed_broken_pagebreak_tail")
        out = new

    # Repair malformed inline page-number comments like:
    # <!-- PageNumber="Page 19 <table>
    new = _BROKEN_PAGENUM_WITH_TABLE_RE.sub("<table>", out)
    if new != out:
        repairs.append("removed_broken_pagenumber_comment")
        out = new

    # Repair bare table tags missing opening `<` at line start (e.g. `tr>`).
    new = _BROKEN_TABLE_ROW_OPEN_RE.sub(lambda m: f"{m.group('indent')}<{m.group(2)}>", out)
    if new != out:
        repairs.append("fixed_missing_angle_table_tag")
        out = new

    # Repair stranded close tags missing opening `<` (e.g. `/tr>`, `/td`).
    new = _BROKEN_TABLE_ROW_CLOSE_RE.sub(lambda m: f"{m.group('indent')}</{m.group(2)}>", out)
    if new != out:
        repairs.append("fixed_missing_angle_close_table_tag")
        out = new

    new = _BROKEN_TABLE_ROW_CLOSE_NO_BRACKET_RE.sub(lambda m: f"{m.group('indent')}</{m.group(2)}>", out)
    if new != out:
        repairs.append("fixed_stranded_table_close_token")
        out = new

    # Repair broken joins like "/tr<table>" by closing then opening table.
    new = _BROKEN_TABLE_JOIN_NO_ANGLE_CLOSE_RE.sub(lambda m: f"</{m.group(1)}>\n<table>", out)
    if new != out:
        repairs.append("fixed_broken_table_join_no_angle_close")
        out = new

    return out, repairs


def classify_parser_repair_severity(repairs: list[str]) -> tuple[str, int]:
    """Return deterministic parser corruption severity and score.

    Score range: 0 (none) to 100 (high corruption risk).
    """
    if not repairs:
        return "none", 0

    severe_count = sum(1 for r in repairs if r in _SEVERE_REPAIRS)
    medium_count = sum(1 for r in repairs if r in _MEDIUM_REPAIRS)
    low_count = max(0, len(repairs) - severe_count - medium_count)
    score = min(100, severe_count * 40 + medium_count * 18 + low_count * 8)

    if severe_count > 0 or score >= 55:
        return "high", score
    if medium_count > 0 or score >= 25:
        return "medium", score
    return "low", score
