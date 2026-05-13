"""Pin the three-bucket status taxonomy from Spec 008.

The mapping table in ``status_bucket.py`` is load-bearing —
``Action Required`` vs ``Needs Attention`` is the difference
between "the operator must investigate today" and "the operator
should look at this when convenient" on the client-shareable
report. HITL approval flipping a non-compliant rule to compliant
is the operator-override path.
"""

from __future__ import annotations

import pytest

from app.compliance.report_renderer.status_bucket import (
    LABELS,
    PRIORITY,
    bucket_status,
)


# ── Raw status → bucket (no HITL) ──────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("compliant", "compliant"),
    ("non_compliant", "action_required"),
    ("uncertain", "needs_attention"),
    ("error", "needs_attention"),
    ("not_applicable", None),
    ("", "needs_attention"),  # unknown / empty defaults to surfacing the row
    ("garbled", "needs_attention"),
])
def test_bucket_status_without_hitl(raw, expected) -> None:
    assert bucket_status(raw) == expected


# ── HITL approval flips non-compliant / uncertain → compliant ──


def test_user_approved_flips_non_compliant_to_compliant() -> None:
    """Operator manually approving an OCR-misread non-compliant
    finding flips the row's badge to Compliant. The operator's
    verdict is the source of truth once they've reviewed."""

    assert bucket_status("non_compliant", "user_approved") == "compliant"


def test_user_approved_flips_uncertain_to_compliant() -> None:
    assert bucket_status("uncertain", "user_approved") == "compliant"


def test_user_approved_does_not_change_compliant() -> None:
    """Already-compliant + approved stays compliant (no-op)."""

    assert bucket_status("compliant", "user_approved") == "compliant"


def test_user_approved_does_not_change_not_applicable() -> None:
    """``user_approved`` on a not-applicable row still excludes it —
    not_applicable means "rule doesn't fire on this doc"; no
    HITL override can repurpose it."""

    assert bucket_status("not_applicable", "user_approved") is None


def test_user_rejected_keeps_non_compliant_as_action_required() -> None:
    """The operator explicitly REJECTED the finding (e.g. they
    confirmed the OCR-read is correct and the gap is real).
    Status stays at action_required."""

    assert bucket_status("non_compliant", "user_rejected") == "action_required"


def test_auto_approved_does_not_flip_failures() -> None:
    """``auto_approved`` is the system's default state on
    high-confidence COMPLIANT findings. It must NOT flip a
    non-compliant finding to compliant — that would mask real
    failures the operator never reviewed."""

    assert bucket_status("non_compliant", "auto_approved") == "action_required"
    assert bucket_status("uncertain", "auto_approved") == "needs_attention"


# ── Labels match the reference verbatim ────────────────────────


def test_labels_match_reference_pdf_wording() -> None:
    """The client reference uses "Compliant" / "Action Required"
    (Akhilesh's pointer renames the reference's "Attention" to
    "Needs Attention"). The display labels in this module are the
    client-facing strings rendered into the badge — any rename
    breaks the visual match. Pin them."""

    assert LABELS["compliant"] == "Compliant"
    assert LABELS["action_required"] == "Action Required"
    assert LABELS["needs_attention"] == "Needs Attention"


def test_priority_orders_action_items_before_compliant() -> None:
    """Sort priority puts ``action_required`` first, then
    ``needs_attention``, then ``compliant``. The row table is
    sorted by this priority so the operator's eye lands on action
    items at the top of the page."""

    assert PRIORITY["action_required"] < PRIORITY["needs_attention"]
    assert PRIORITY["needs_attention"] < PRIORITY["compliant"]
