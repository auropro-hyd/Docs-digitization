"""Map raw rule statuses to the three-bucket Spec 008 taxonomy.

The exported and on-screen rule-centric reports collapse every
underlying status + HITL state into one of three buckets:

  * ``compliant`` — green, displayed as "Compliant"
  * ``action_required`` — orange, displayed as "Action Required"
  * ``needs_attention`` — amber, displayed as "Needs Attention"

Rules with ``status="not_applicable"`` are excluded from the
report entirely (the bucketer returns ``None``).

The HITL override is the operator's manual verdict. When an
operator approves a non-compliant finding (e.g. because the OCR
mis-read a cell), the rule's row flips to ``compliant``.
"""

from __future__ import annotations

from typing import Literal

ComplianceKind = Literal["compliant", "action_required", "needs_attention"]


# Display labels exactly matching the client reference PDF + Akhilesh's pointer.
LABELS: dict[ComplianceKind, str] = {
    "compliant": "Compliant",
    "action_required": "Action Required",
    "needs_attention": "Needs Attention",
}


# Sort priority — used to put action items at the top of the rule table.
PRIORITY: dict[ComplianceKind, int] = {
    "action_required": 0,
    "needs_attention": 1,
    "compliant": 2,
}


# HITL override statuses recognised by this module.
_HITL_APPROVED: frozenset[str] = frozenset({
    "user_approved",
    "auto_approved",  # The system auto-approves high-confidence compliant findings.
})
_HITL_REJECTED: frozenset[str] = frozenset({
    "user_rejected",
})


def bucket_status(
    status: str,
    hitl_status: str | None = None,
) -> ComplianceKind | None:
    """Map ``(raw_status, hitl_status)`` to one of three buckets, or
    ``None`` for ``not_applicable`` (caller drops the row).

    Truth table:

    +------------------+------------------+---------------------+
    | status           | hitl_status      | bucket              |
    +==================+==================+=====================+
    | compliant        | (any)            | compliant           |
    +------------------+------------------+---------------------+
    | non_compliant    | user_approved    | compliant           |
    +------------------+------------------+---------------------+
    | non_compliant    | (none / other)   | action_required     |
    +------------------+------------------+---------------------+
    | uncertain        | user_approved    | compliant           |
    +------------------+------------------+---------------------+
    | uncertain        | (none / other)   | needs_attention     |
    +------------------+------------------+---------------------+
    | error            | (any)            | needs_attention     |
    +------------------+------------------+---------------------+
    | not_applicable   | (any)            | None (row excluded) |
    +------------------+------------------+---------------------+
    """

    s = (status or "").strip().lower()
    if s == "not_applicable":
        return None

    # HITL approval always wins — the operator's manual verdict is
    # the source of truth once they've reviewed.
    if hitl_status == "user_approved" and s in {"non_compliant", "uncertain"}:
        return "compliant"

    if s == "compliant":
        return "compliant"
    if s == "non_compliant":
        return "action_required"
    if s in {"uncertain", "error"}:
        return "needs_attention"

    # Unknown status — surface as needs_attention so it doesn't
    # silently disappear AND the operator sees something is off.
    return "needs_attention"
