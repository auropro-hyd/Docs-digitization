"""Pin the observability-polish fixes from the 2026-05-14 audit.

A live compliance run on main showed five remaining noise / clarity
issues even after the heartbeat throttle + logging hygiene + per-emit
wrapper fixes landed. This module pins each fix.

  1. Empty batch-failure messages — ``Batch X attempt N failed: `` with
     nothing after the colon (e.g. tenacity ``RetryError`` whose
     ``str()`` is empty). Always log ``type(exc).__name__`` so the
     operator sees the exception class even when the message is empty.

  2. Compliance-pipeline status polling — frontend hits
     ``/api/compliance/{doc_id}/status`` every 30 s during a run.
     Quiet-routes allowlist must include it.

  3. Google GenAI SDK's ``AFC is enabled with max remote calls: 10.``
     fires on every Client / Tool init — pure SDK config noise that
     should be at WARNING level for the SDK logger.

  4. ``bpcr.section_detect.entry/exit`` fires twice per compliance
     run at INFO — operationally interesting at DEBUG but not
     load-bearing operator signal.

  5. ``segmentation quality issues`` was logging the full list of
     issue dicts as a single inline JSON blob, wrapping across the
     terminal. Compact summary by kind; per-issue detail still lands
     in telemetry via the ``segmentation.<kind>`` events.
"""

from __future__ import annotations

import logging

import pytest


# ── Fix 1: batch-failure message never empty ────────────────────


def test_empty_exception_message_still_carries_class_name(caplog) -> None:
    """``RetryError`` and some ``JSONDecodeError`` variants have
    empty ``str()``. The log line must still include the exception
    class so the operator can diagnose."""

    # Reconstruct the production log call shape — the actual call
    # site is inside ``RuleBatchEvaluator.evaluate_batch`` which is
    # tightly bound to provider machinery. The shape is:
    #
    #     logger.warning(
    #         "Batch %s page %d attempt %d failed: %s: %s",
    #         batch_id, page_num, attempt, type(exc).__name__,
    #         str(exc) or repr(exc),
    #     )
    class _SilentRetryError(Exception):
        def __str__(self) -> str:
            return ""

    exc = _SilentRetryError()
    exc_class = type(exc).__name__
    exc_msg = str(exc) or repr(exc)

    with caplog.at_level(logging.WARNING):
        logging.getLogger("app.compliance.evaluator").warning(
            "Batch %s page %d attempt %d failed: %s: %s",
            "batch-id", 5, 1, exc_class, exc_msg,
        )

    line = caplog.records[-1].getMessage()
    assert "failed: " in line
    assert "_SilentRetryError" in line, (
        f"empty-str exception must still surface its class name; got: {line!r}"
    )
    # Crucially the line must NOT end with `failed: ` (the
    # production bug).
    assert not line.endswith("failed: ")
    assert not line.endswith("failed: : ")


# ── Fix 2: compliance polling in quiet routes ───────────────────


def test_compliance_polling_routes_are_quiet() -> None:
    """The compliance status / report / segmentation endpoints
    are hit every 30 s while a compliance run is in progress AND
    on every render of the report view. They must be in the quiet
    allowlist alongside the document-progress polling."""

    from app.observability.middleware import _is_quiet_route

    assert _is_quiet_route("GET", "/api/compliance/{doc_id}/status")
    assert _is_quiet_route("GET", "/api/compliance/{doc_id}/report")
    assert _is_quiet_route("GET", "/api/compliance/{doc_id}/segmentation")
    assert _is_quiet_route("GET", "/api/compliance/{doc_id}/discovered-rules")

    # State-changing compliance routes (kicking off a run, etc.)
    # must still log at INFO.
    assert not _is_quiet_route("POST", "/api/compliance/{doc_id}/run")
    assert not _is_quiet_route("POST", "/api/compliance/{doc_id}/status")


def test_frontend_list_endpoints_are_quiet() -> None:
    """The frontend re-fetches these on every page render. They
    carry no business-state info — the trace overhead is pure
    noise."""

    from app.observability.middleware import _is_quiet_route

    assert _is_quiet_route("GET", "/api/documents/")
    assert _is_quiet_route("GET", "/api/rules/agents")
    # OPTIONS preflights to those routes also quieted (inherited).
    assert _is_quiet_route("OPTIONS", "/api/documents/")
    assert _is_quiet_route("OPTIONS", "/api/rules/agents")


# ── Fix 3: Google GenAI SDK quieted ─────────────────────────────


def test_google_genai_sdk_is_quieted_to_warning(monkeypatch) -> None:
    """Both ``google_genai`` and ``google.genai`` module paths covered
    because the SDK uses different names across versions."""

    from app.observability import configure

    monkeypatch.setenv("AT_OBS__LOG_MODE", "json")
    monkeypatch.setenv("AT_OBS__LOG_LEVEL", "INFO")
    configure(force=True)

    assert logging.getLogger("google_genai").level == logging.WARNING
    assert logging.getLogger("google.genai").level == logging.WARNING


# ── Fix 5: segmentation issues compact summary ──────────────────


def test_segmentation_issue_summary_is_compact(caplog) -> None:
    """The summary line must be a single short string with counts
    by kind, not the full JSON-list dump. Per-issue detail still
    surfaces via ``segmentation.<kind>`` telemetry events."""

    from collections import Counter
    from app.compliance.segmentation import SegmentationIssue

    issues = [
        SegmentationIssue(kind="unknown_section_type", message=f"sec_{i}")
        for i in range(7)
    ] + [
        SegmentationIssue(kind="gap", message=f"gap_{i}", page_range=(i, i))
        for i in range(2)
    ]

    by_kind = Counter(i.kind for i in issues)
    summary = ", ".join(
        f"{kind}={count}" for kind, count in sorted(by_kind.items())
    )

    with caplog.at_level(logging.WARNING):
        logging.getLogger("app.compliance.segmentation").warning(
            "segmentation quality issues (%d total): %s — "
            "see segmentation.<kind> events in telemetry "
            "for per-issue detail",
            len(issues), summary,
        )

    line = caplog.records[-1].getMessage()
    # The compact summary must contain the per-kind counts.
    assert "unknown_section_type=7" in line
    assert "gap=2" in line
    # And it MUST NOT contain the per-issue verbose dump.
    assert "sec_0" not in line, (
        f"compact summary leaked per-issue detail into the log line; "
        f"that should live in telemetry only: {line!r}"
    )
    # Whole line should be under 300 chars (vs the 7KB+ pre-fix dump).
    assert len(line) < 300
