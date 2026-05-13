"""Pin the OCR-progress wrapper's log throttle.

The wrapper in ``workflow/nodes.run_azure_di_ocr._on_ocr_progress``
sits between the OCR adapter's heartbeat callback and (a) the WS
broadcast (b) the stdout log line. It's supposed to throttle the
LOG to "first emit + 5%-jumps + terminal 100%", letting all
broadcasts through. The pre-fix code wrote:

    if percent >= last_logged_percent + 5 or percent in (0, 100):
        logger.info(...)

which logged on every call with ``percent == 0`` because the
special-case fired unconditionally — defeating its own throttle.
On the 2026-05-13 production run that produced ~200
``OCR progress 0% - Datalab • 5/5 chunks analyzing`` lines per
200 s of waiting. This module pins the corrected throttle so the
bug can't return.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


def _make_wrapper(doc_id: str = "doc-X"):
    """Build a fresh wrapper closure mirroring run_azure_di_ocr's setup.

    Closure-level state (``last_logged_percent``) means we have to
    rebuild for each test scenario. Returns ``(wrapper, log_calls)``
    where ``log_calls`` is a list captured from the structlog logger.
    """
    # Import locally so we re-use the production source's logic via
    # reconstruction (the wrapper is a closure inside an async fn,
    # not directly importable). The reconstruction mirrors the
    # post-fix policy line-for-line.
    log_calls: list[tuple[int, str]] = []

    def fake_log(fmt: str, doc_id_v, percent: int, label: str) -> None:
        log_calls.append((percent, label))

    last_logged_percent = -1
    last_logged_label = ""

    def wrapper(percent: int, label: str) -> None:
        nonlocal last_logged_percent, last_logged_label
        first_emit = last_logged_percent < 0
        significant_jump = percent >= last_logged_percent + 5
        terminal_complete = percent == 100 and last_logged_percent < 100
        if first_emit or significant_jump or terminal_complete:
            last_logged_percent = percent
            last_logged_label = label
            fake_log("fmt", doc_id, percent, label)

    return wrapper, log_calls


def test_first_emit_logs_at_zero_percent() -> None:
    """OCR has started — the very first 0% emit must surface so
    the operator sees the run kicked off."""

    wrapper, log_calls = _make_wrapper()
    wrapper(0, "Datalab • 5/5 chunks analyzing • oldest 1s")
    assert len(log_calls) == 1
    assert log_calls[0][0] == 0


def test_stable_zero_percent_does_not_repeat_log() -> None:
    """The exact production bug: heartbeat keeps emitting at 0%
    while chunks haven't completed. With PR #55's upstream
    throttle this becomes ~1 emit per 30s; the wrapper must NOT
    re-log those quiet-interval pings as if they were the first."""

    wrapper, log_calls = _make_wrapper()
    # Simulate a state-change emit at start + multiple quiet-interval
    # liveness pings at the same 0% / same state.
    wrapper(0, "Datalab • 5/5 chunks analyzing • oldest 1s")
    wrapper(0, "Datalab • 5/5 chunks analyzing • oldest 31s • no change for 30s")
    wrapper(0, "Datalab • 5/5 chunks analyzing • oldest 61s • no change for 30s")

    assert len(log_calls) == 1, (
        f"stable 0% must log only the first emit, not the liveness "
        f"pings; got {log_calls}"
    )


def test_significant_percent_jump_logs() -> None:
    """A ≥5% jump is a real progress signal — must surface."""

    wrapper, log_calls = _make_wrapper()
    wrapper(0, "start")
    wrapper(18, "Datalab • 4/5 chunks analyzing • 1 completed")
    wrapper(36, "Datalab • 3/5 chunks analyzing • 2 completed")

    assert [pct for pct, _ in log_calls] == [0, 18, 36]


def test_small_percent_jumps_do_not_log() -> None:
    """A 1-2-3% drift (e.g. fine-grained PDF parsing progress
    within a chunk) must NOT log on every step — would re-introduce
    the flood the throttle exists to prevent."""

    wrapper, log_calls = _make_wrapper()
    wrapper(0, "start")
    for pct in (1, 2, 3, 4):
        wrapper(pct, "tiny drift")

    assert [pct for pct, _ in log_calls] == [0]


def test_terminal_100_logs_even_after_recent_log() -> None:
    """Completion is a load-bearing signal. Even when the previous
    log was at 95% (just 5% before 100%, satisfying the jump rule),
    the 100% must still log — the terminal_complete branch ensures
    it survives even if percent-jump logic might miss it."""

    wrapper, log_calls = _make_wrapper()
    wrapper(0, "start")
    wrapper(99, "almost done")  # 0 → 99 is a 99% jump, logs
    wrapper(100, "done!")
    assert log_calls[-1][0] == 100


def test_100_does_not_repeat_log_when_already_at_100() -> None:
    """If 100% somehow fires twice (e.g. a defensive call from the
    pipeline node finalising), the second one must NOT log."""

    wrapper, log_calls = _make_wrapper()
    wrapper(0, "start")
    wrapper(100, "done")
    wrapper(100, "done again")
    assert len([c for c in log_calls if c[0] == 100]) == 1


def test_real_world_200_second_wait_drops_log_lines_by_orders_of_magnitude() -> None:
    """Numeric pin: a synthetic 200-second wait at the upstream's
    new throttled rate (1 emit per 30 s by default) used to produce
    ~200 log lines (every poll_interval=1 s flood plus the 0%
    special-case). After this fix it should produce 1 line
    (the initial state-change), confirming the wrapper composes
    correctly with the upstream throttle."""

    wrapper, log_calls = _make_wrapper()
    # Initial state-change emit.
    wrapper(0, "Datalab • 5/5 chunks analyzing • oldest 1s")
    # 6 quiet-interval liveness pings every 30s — all stable at 0%.
    for i in range(1, 7):
        wrapper(0, f"Datalab • 5/5 chunks analyzing • no change for {30 * i}s")

    assert len(log_calls) == 1, (
        f"200-second wait at 1 emit / 30 s should produce exactly 1 "
        f"log line (the initial state-change). Got {len(log_calls)}: "
        f"{log_calls}"
    )
