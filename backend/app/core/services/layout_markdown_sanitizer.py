"""Deterministic cleanup for malformed layout-markdown fragments."""

from __future__ import annotations

import re

_PAGEBREAK_VARIANT_RE = re.compile(r"(?:<!--\s*)?-{0,2}\s*PageBreak\s*-->\s*", re.IGNORECASE)
_TRUNCATED_COMMENT_RE = re.compile(r"(?:<!\s*|<!--\s*)$")
_ORPHAN_TABLE_WRAPPER_RE = re.compile(r"</table>\s*</td>\s*</tr>\s*</table>", re.IGNORECASE)
_REPEATED_TABLE_OPEN_RE = re.compile(r"<{2,}\s*table>", re.IGNORECASE)
_BROKEN_TABLE_JOIN_RE = re.compile(r"</t\s*<table>", re.IGNORECASE)


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

    return out, repairs
