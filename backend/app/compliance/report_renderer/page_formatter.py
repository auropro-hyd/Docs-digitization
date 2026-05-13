"""Format an ordered list of page integers into the reference PDF's
display syntax.

Examples (from the client reference):

  * ``[]``                              → ``""``
  * ``[103]``                           → ``"PAGE:103"``
  * ``[6, 9, 31]``                      → ``"PAGE:6, 9, 31"``
  * ``[36, 37, 38, 39, 40, 41, 42]``    → ``"PAGE:36 to 42"``
  * ``[6, 7, 8, 9, 10, 11, 12, 13]``    → ``"PAGE:6 to 13"``
  * ``[1, 5, 6, 7, 8, 9, 10]``          → ``"PAGE:1, 5 to 10"``

Rule: contiguous run of ``MIN_RANGE_RUN`` or more pages renders as
``N to M``; otherwise comma-separated.
"""

from __future__ import annotations

MIN_RANGE_RUN: int = 3
"""Minimum consecutive pages required to render as a ``N to M`` range
instead of comma-separated. Three matches the reference PDF — e.g.
"PAGE:6, 9, 31" stays comma-separated (no 3-run) while "PAGE:36 to 42"
collapses (7-run)."""


def format_pages(page_nums: list[int]) -> str:
    """Compress an ordered set of page integers into the reference's
    display syntax. Pure function; no I/O.

    Sorts and de-duplicates input. Empty input returns ``""``.
    """

    if not page_nums:
        return ""

    sorted_pages = sorted({int(p) for p in page_nums if isinstance(p, (int, float)) and p > 0})
    if not sorted_pages:
        return ""

    # Group into consecutive runs.
    runs: list[list[int]] = []
    current: list[int] = [sorted_pages[0]]
    for p in sorted_pages[1:]:
        if p == current[-1] + 1:
            current.append(p)
        else:
            runs.append(current)
            current = [p]
    runs.append(current)

    parts: list[str] = []
    for run in runs:
        if len(run) >= MIN_RANGE_RUN:
            parts.append(f"{run[0]} to {run[-1]}")
        else:
            parts.extend(str(p) for p in run)

    return f"PAGE:{', '.join(parts)}"
