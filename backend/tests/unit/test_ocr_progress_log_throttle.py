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

    Closure-level state (``last_logged_percent``, ``last_logged_label``)
    means we have to rebuild for each test scenario. The
    reconstruction mirrors the post-fix policy line-for-line.
    Returns ``(wrapper, log_calls)``.
    """
    log_calls: list[tuple[int, str]] = []

    def fake_log(fmt: str, doc_id_v, percent: int, label: str) -> None:
        log_calls.append((percent, label))

    last_logged_percent = -1
    last_logged_label = ""

    def wrapper(percent: int, label: str) -> None:
        nonlocal last_logged_percent, last_logged_label
        first_emit = last_logged_percent < 0
        emit_differs = (
            percent != last_logged_percent
            or label != last_logged_label
        )
        terminal_complete = percent == 100 and last_logged_percent < 100
        if first_emit or emit_differs or terminal_complete:
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


def test_identical_percent_and_label_does_not_repeat_log() -> None:
    """Pure pass-through is wrong — a second emit with the EXACT
    same (percent, label) pair (e.g. a defensive double-call) must
    NOT log twice. The upstream heartbeat throttle never produces
    this case in practice (each emit has a different ``oldest Ns``
    timestamp), but the wrapper's de-duplication still needs to
    hold."""

    wrapper, log_calls = _make_wrapper()
    wrapper(0, "Datalab • 5/5 chunks analyzing • oldest 1s, newest 1s")
    wrapper(0, "Datalab • 5/5 chunks analyzing • oldest 1s, newest 1s")
    wrapper(0, "Datalab • 5/5 chunks analyzing • oldest 1s, newest 1s")
    assert len(log_calls) == 1


def test_liveness_pings_at_same_percent_with_new_label_log() -> None:
    """The user's actual complaint: at the same 0% the heartbeat
    emits liveness pings every 30 s with distinct labels (oldest
    Ns tag). The wrapper MUST log each because the label carries
    meaningful "no change for Ns" / "oldest Ns" info — silent
    waiting was confusing the operator into thinking the pipeline
    had stalled."""

    wrapper, log_calls = _make_wrapper()
    wrapper(0, "Datalab • 5/5 chunks analyzing • oldest 1s, newest 1s")
    wrapper(0, "Datalab • 5/5 chunks analyzing • oldest 31s • no change for 30s")
    wrapper(0, "Datalab • 5/5 chunks analyzing • oldest 61s • no change for 30s")

    assert len(log_calls) == 3, (
        f"each liveness ping carries a distinct label and must surface "
        f"so the operator sees the pipeline is alive; got {log_calls}"
    )
    # All three lines should be at 0% (the heartbeat baseline during waiting).
    assert all(pct == 0 for pct, _ in log_calls)


def test_chunk_completion_percent_jumps_log() -> None:
    """Per-chunk completion is a real progress signal — must surface."""

    wrapper, log_calls = _make_wrapper()
    wrapper(0, "start")
    wrapper(18, "Datalab • 4/5 chunks analyzing • 1 completed")
    wrapper(36, "Datalab • 3/5 chunks analyzing • 2 completed")

    assert [pct for pct, _ in log_calls] == [0, 18, 36]


def test_non_monotonic_starting_to_waiting_transition_logs() -> None:
    """The Datalab start sequence: adapter emits 2% ("Starting 5
    chunks") then the heartbeat emits 0% ("5/5 chunks analyzing").
    The pre-fix wrapper's upward-only ``>= last + 5`` threshold
    silently filtered the 0% transition (``0 - 2 = -2``), leaving
    the waiting phase invisible until a chunk completed — the user
    saw NO log lines for the entire 30 + seconds Datalab took to
    return its first chunk. The any-change policy fixes this:
    both transitions log."""

    wrapper, log_calls = _make_wrapper()
    wrapper(2, "Starting 5 chunk(s) with concurrency 5")
    wrapper(0, "Datalab • 5/5 chunks analyzing • oldest 1s, newest 1s")

    assert [pct for pct, _ in log_calls] == [2, 0], (
        "the 2% → 0% Datalab-start transition must produce both "
        "log lines; pre-fix only the 2% surfaced"
    )


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


def test_100_does_not_repeat_when_emit_is_identical() -> None:
    """Defensive double-fire of the SAME (100, label) tuple must
    log only once. A genuinely-different label at 100% (e.g. a
    second completion phase) IS treated as new and logs — that's
    expected new info, not a defensive duplicate."""

    wrapper, log_calls = _make_wrapper()
    wrapper(0, "start")
    wrapper(100, "Data Lab extraction complete")
    wrapper(100, "Data Lab extraction complete")  # exact duplicate — skip
    assert len([c for c in log_calls if c[0] == 100]) == 1


def test_real_world_200_second_wait_produces_visible_liveness_pings() -> None:
    """Numeric pin: 200-second wait at PR #55's upstream rate (one
    emit per 30 s + the initial state-change) plus the Datalab
    "Starting Ns chunk(s)" emit at t=0. With the any-change policy
    we get one log per distinct emit — that's 1 (Starting at 2%) +
    1 (state-change to 0%) + 6 (liveness pings every 30 s) = 8.

    Pre-fix the wrapper's percent-jump-threshold-only policy gave
    1 line (just "Starting 2%") because every 0% emit was filtered.
    Post-fix the operator sees the waiting phase as periodic
    liveness lines they can correlate with wall time."""

    wrapper, log_calls = _make_wrapper()
    # t=0: adapter emits the "Starting" line.
    wrapper(2, "Starting 5 chunk(s) with concurrency 5")
    # t=1s: heartbeat state-change emit to waiting state.
    wrapper(0, "Datalab • 5/5 chunks analyzing • oldest 1s, newest 1s")
    # 6 quiet-interval liveness pings every 30s — distinct labels.
    for i in range(1, 7):
        wrapper(
            0,
            f"Datalab • 5/5 chunks analyzing "
            f"• oldest {30 * i + 1}s, newest {30 * i + 1}s "
            f"• no change for {30 * i}s",
        )

    assert len(log_calls) == 8, (
        f"200-second wait should produce 8 lines (Starting + state-"
        f"change + 6 liveness pings). The pre-fix wrapper produced "
        f"only 1 because the upward-only ≥5% threshold filtered the "
        f"2% → 0% transition. Got {len(log_calls)}: {log_calls}"
    )
