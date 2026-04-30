"""Tests for the aggregate heartbeat that replaced per-chunk heartbeats.

The original UX bug this fixes: with 8 concurrent chunks each emitting
their own per-second heartbeat, the upstream WS throttle (1 broadcast
per second per doc) could only let one through — and chunk 1 won the
asyncio race repeatedly. The user saw ``Chunk 1/8 (pages 0-24) —
analyzing (46s)`` re-emitted forever and assumed nothing else was
running, when in fact all 8 chunks were processing concurrently.

The fix centralizes the heartbeat at ``extract()`` level: chunks
register themselves in a shared ``in_flight`` map on entry and remove
themselves on exit; a single coroutine reads the map every
``poll_interval`` and emits one aggregate label covering all
in-flight work. These tests pin that contract.
"""

from __future__ import annotations

import asyncio

import pytest

from app.adapters.ocr.datalab import DatalabOCRAdapter
from app.config.settings import DatalabConfig


def _adapter() -> DatalabOCRAdapter:
    cfg = DatalabConfig(
        api_key="a-real-looking-key-1234567890",
        max_polls=100,
        poll_interval=1,
        submit_max_retries=1,
        submit_retry_base_delay=0.1,
        chunk_pages=10,
        max_concurrent_chunks=8,
    )
    return DatalabOCRAdapter(cfg)


@pytest.mark.asyncio
async def test_aggregate_heartbeat_reports_all_in_flight_chunks() -> None:
    """The aggregate label names the chunk count, oldest age, newest
    age, and any completed count — covering the full picture in one
    broadcast. Without this, a reviewer sees only one chunk's label
    (whichever wins the asyncio race) and concludes the others are
    stuck.
    """

    adapter = _adapter()
    # Force a faster poll for the test so we don't burn 1s of wall
    # clock waiting for the first tick.
    adapter._config.poll_interval = 0.05  # type: ignore[misc]

    loop = asyncio.get_running_loop()
    in_flight = {
        0: ("Chunk 1/8 (pages 0-24)", loop.time() - 5.0),  # oldest
        3: ("Chunk 4/8 (pages 75-99)", loop.time() - 2.0),
        7: ("Chunk 8/8 (pages 175-184)", loop.time() - 0.5),  # newest
    }
    completed_counter = {"n": 1}
    ticks: list[tuple[int, str]] = []

    def cb(percent: int, label: str) -> None:
        ticks.append((percent, label))

    task = asyncio.create_task(
        adapter._run_aggregate_heartbeat(
            in_flight=in_flight,
            completed_counter=completed_counter,
            total_chunks=8,
            progress_callback=cb,
        )
    )
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert ticks, "expected ≥1 aggregate tick during 200ms run"
    pct, label = ticks[0]
    assert pct == int(1 / 8 * 90), (
        f"baseline percent must reflect completed_counter ({pct})"
    )
    assert "3/8 chunks analyzing" in label, label
    assert "oldest" in label and "newest" in label, label
    assert "1 completed" in label, label


@pytest.mark.asyncio
async def test_aggregate_heartbeat_skips_emit_when_no_chunks_in_flight() -> None:
    """During the brief gap when all chunks just finished and the
    completion broadcast hasn't fired yet, the aggregate must not
    emit a confusing "0 chunks analyzing" tick. The 100% boundary
    is owned by the chunk-completion path."""

    adapter = _adapter()
    adapter._config.poll_interval = 0.05  # type: ignore[misc]

    in_flight: dict[int, tuple[str, float]] = {}
    completed_counter = {"n": 8}
    ticks: list[tuple[int, str]] = []

    task = asyncio.create_task(
        adapter._run_aggregate_heartbeat(
            in_flight=in_flight,
            completed_counter=completed_counter,
            total_chunks=8,
            progress_callback=lambda p, l: ticks.append((p, l)),
        )
    )
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert ticks == [], (
        f"expected zero ticks when nothing is in flight; got {ticks}"
    )


@pytest.mark.asyncio
async def test_aggregate_heartbeat_stops_cleanly_on_cancel() -> None:
    """``extract()`` cancels the heartbeat task when ``asyncio.gather``
    over chunks resolves. The task must absorb its own
    ``CancelledError`` so the surrounding ``await`` doesn't surface
    a stray exception to the pipeline."""

    adapter = _adapter()
    adapter._config.poll_interval = 0.05  # type: ignore[misc]

    in_flight = {0: ("Chunk 1/8", asyncio.get_running_loop().time())}

    task = asyncio.create_task(
        adapter._run_aggregate_heartbeat(
            in_flight=in_flight,
            completed_counter={"n": 0},
            total_chunks=8,
            progress_callback=lambda p, l: None,
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    # Should resolve without raising.
    await asyncio.wait_for(task, timeout=1.0)
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_aggregate_heartbeat_no_op_when_progress_callback_is_none() -> None:
    """A run with no callback wired (e.g. the BMR pipeline's
    SidecarExtractor path) must short-circuit immediately rather
    than spin a useless loop."""

    adapter = _adapter()
    adapter._config.poll_interval = 0.05  # type: ignore[misc]

    task = asyncio.create_task(
        adapter._run_aggregate_heartbeat(
            in_flight={0: ("Chunk 1/8", 0.0)},
            completed_counter={"n": 0},
            total_chunks=8,
            progress_callback=None,
        )
    )
    # The coroutine should return immediately when there's no callback.
    await asyncio.wait_for(task, timeout=0.2)
    assert task.done()
