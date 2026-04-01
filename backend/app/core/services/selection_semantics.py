"""Heuristics for checklist selection-mark semantics."""

from __future__ import annotations

import re
from typing import Any

_YES_RE = re.compile(r"\bYES\b", re.IGNORECASE)
_NO_RE = re.compile(r"\bNO\b", re.IGNORECASE)
_NA_RE = re.compile(r"\bN/?A\b|\bNOT\s+APPLICABLE\b", re.IGNORECASE)


def summarize_selection_semantics(markdown: str, selection_marks: list[dict[str, Any]]) -> dict[str, Any]:
    """Produce deterministic semantics summary for selection-heavy pages.

    This is an evidence summary only; it does not mutate extracted values.
    """
    text = markdown or ""
    marks = selection_marks or []
    selected = sum(1 for m in marks if (m.get("state") or "").lower() == "selected")
    unselected = sum(1 for m in marks if (m.get("state") or "").lower() == "unselected")
    unknown = max(0, len(marks) - selected - unselected)

    has_yes = bool(_YES_RE.search(text))
    has_no = bool(_NO_RE.search(text))
    has_na = bool(_NA_RE.search(text))
    tri_state = has_yes and has_no and has_na

    ambiguous = False
    reasons: list[str] = []
    if selected > 0 and not (has_yes or has_no or has_na):
        ambiguous = True
        reasons.append("selected_without_checklist_headers")
    if selected > 0 and all(m.get("bounding_region") is None for m in marks):
        ambiguous = True
        reasons.append("selected_marks_without_geometry")
    if tri_state and selected == 0:
        ambiguous = True
        reasons.append("tri_state_headers_without_selected_marks")

    return {
        "has_selection_marks": len(marks) > 0,
        "selected_count": selected,
        "unselected_count": unselected,
        "unknown_count": unknown,
        "checklist_headers": {
            "yes": has_yes,
            "no": has_no,
            "na": has_na,
            "tri_state": tri_state,
        },
        "ambiguous": ambiguous,
        "ambiguity_reasons": reasons,
    }
