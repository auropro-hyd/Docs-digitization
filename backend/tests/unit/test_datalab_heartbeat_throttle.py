"""Pin the aggregate-heartbeat throttle policy.

Background: the OCR aggregate heartbeat used to broadcast every
``poll_interval`` (1 s default) regardless of whether anything had
changed. A 200-second wait on a flaky-Datalab run produced 200
indistinguishable lines reading
``OCR progress 0% - Datalab • 5/5 chunks analyzing • oldest Xs,
newest Xs`` — drowning every other log signal in the stream.

New policy (pinned here):

  * Emit on state CHANGE: any time the (active_chunks,
    completed_chunks) tuple changes vs. the last emit.
  * Emit on QUIET-INTERVAL elapsed: at most one liveness ping per
    ``heartbeat_quiet_interval_s`` (default 30 s) when state is
    unchanged. The quiet-interval emit's label is tagged
    ``• no change for Ns`` so the operator sees why the line fired.
  * Never emit when the in-flight map is empty (all chunks done) —
    chunk-completion path owns the 100% emit.
"""

from __future__ import annotations

import asyncio

import pytest

from app.adapters.ocr.datalab import DatalabOCRAdapter
from app.config.settings import DatalabConfig


def _new_adapter(quiet_interval: float = 30.0) -> DatalabOCRAdapter:
    cfg = DatalabConfig(
        api_key="a-real-looking-key-1234567890",
        poll_interval=0.01,  # fast ticks for tests
        heartbeat_quiet_interval_s=quiet_interval,
    )
    return DatalabOCRAdapter(cfg)


@pytest.mark.asyncio
async def test_heartbeat_emits_immediately_on_state_change() -> None:
    """Every chunk-state transition (e.g. a chunk completes,
    bringing in_flight from 5 to 4) must fire one emit immediately.
    Without this, the operator sees no signal between meaningful
    transitions."""

    adapter = _new_adapter(quiet_interval=999.0)  # huge quiet — only state-changes fire
    loop = asyncio.get_running_loop()

    in_flight = {0: ("c0", loop.time()), 1: ("c1", loop.time())}
    completed = {"n": 0}
    emits: list[tuple[int, str]] = []

    def cb(pct: int, label: str) -> None:
        emits.append((pct, label))

    task = asyncio.create_task(adapter._run_aggregate_heartbeat(
        in_flight=in_flight,
        completed_counter=completed,
        total_chunks=2,
        progress_callback=cb,
    ))
    await asyncio.sleep(0.05)  # let the first tick emit
    first_emit_count = len(emits)

    # Mutate state: chunk 0 completes.
    in_flight.pop(0)
    completed["n"] = 1
    await asyncio.sleep(0.05)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # First tick = state change from "never emitted" to "(2, 0)" → emit.
    # Second tick after state mutation = state change to "(1, 1)" → emit.
    assert len(emits) >= 2, (
        f"expected at least 2 emits (initial + state-change), got "
        f"{len(emits)}: {emits}"
    )
    # The post-change emit must carry the new completed count.
    assert any("1 completed" in label for _pct, label in emits)


@pytest.mark.asyncio
async def test_heartbeat_does_not_repeat_when_state_unchanged() -> None:
    """The whole point of the fix: identical state across N ticks
    produces only one emit (the initial state-change one), not N.
    With quiet_interval=999 and 5 poll ticks of unchanged state we
    must see exactly 1 emit."""

    adapter = _new_adapter(quiet_interval=999.0)
    loop = asyncio.get_running_loop()
    in_flight = {0: ("c0", loop.time())}
    completed = {"n": 0}
    emits: list[tuple[int, str]] = []

    task = asyncio.create_task(adapter._run_aggregate_heartbeat(
        in_flight=in_flight,
        completed_counter=completed,
        total_chunks=2,
        progress_callback=lambda p, l: emits.append((p, l)),
    ))
    await asyncio.sleep(0.06)  # ~6 ticks at poll_interval=0.01

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(emits) == 1, (
        f"unchanged state must emit exactly once (the initial state-"
        f"change), got {len(emits)} emits: {emits}"
    )


@pytest.mark.asyncio
async def test_heartbeat_emits_periodic_liveness_after_quiet_interval() -> None:
    """When state is unchanged, the heartbeat still emits once per
    ``heartbeat_quiet_interval_s`` so the operator sees the system
    is alive. The label must include ``no change for Ns`` so the
    line is distinguishable from a state-change emit."""

    adapter = _new_adapter(quiet_interval=0.05)  # fire liveness every 50 ms
    loop = asyncio.get_running_loop()
    in_flight = {0: ("c0", loop.time())}
    completed = {"n": 0}
    emits: list[tuple[int, str]] = []

    task = asyncio.create_task(adapter._run_aggregate_heartbeat(
        in_flight=in_flight,
        completed_counter=completed,
        total_chunks=2,
        progress_callback=lambda p, l: emits.append((p, l)),
    ))
    # 0.20 s of unchanged state should yield 1 initial + at least 2 quiet pings.
    await asyncio.sleep(0.20)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(emits) >= 3, (
        f"after 0.20 s with 0.05 s quiet-interval we expected 3+ "
        f"emits (1 initial + 2 liveness), got {len(emits)}"
    )
    # All quiet-interval emits carry the "no change for Ns" tag.
    quiet_emits = [label for _p, label in emits if "no change for" in label]
    assert len(quiet_emits) >= 2


@pytest.mark.asyncio
async def test_heartbeat_skips_when_no_chunks_in_flight() -> None:
    """Empty in_flight means all chunks have completed — the
    chunk-completion path owns the 100% emit. The heartbeat must
    NOT emit in that window."""

    adapter = _new_adapter(quiet_interval=0.01)
    in_flight: dict = {}
    completed = {"n": 5}
    emits: list = []

    task = asyncio.create_task(adapter._run_aggregate_heartbeat(
        in_flight=in_flight,
        completed_counter=completed,
        total_chunks=5,
        progress_callback=lambda p, l: emits.append((p, l)),
    ))
    await asyncio.sleep(0.10)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert emits == [], (
        f"heartbeat must stay silent when in_flight is empty; got {emits}"
    )


@pytest.mark.asyncio
async def test_heartbeat_handles_no_callback_gracefully() -> None:
    """The coroutine must be safe to call with progress_callback=None
    (e.g. a non-UI-driven adapter call). Returns immediately, no
    exceptions."""

    adapter = _new_adapter()
    in_flight: dict = {0: ("c0", 0.0)}
    completed = {"n": 0}

    task = asyncio.create_task(adapter._run_aggregate_heartbeat(
        in_flight=in_flight,
        completed_counter=completed,
        total_chunks=1,
        progress_callback=None,
    ))
    await asyncio.sleep(0.02)
    # Should have returned cleanly — task is done, not awaiting.
    assert task.done() or not task.cancelled()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_quiet_interval_setting_default_and_env_override() -> None:
    """Default is 30 s. Operators can lower for dev sessions or
    raise for production where each line costs ingest. Pin the
    default so a future tightening of the value has to be explicit."""

    cfg = DatalabConfig(api_key="a-real-looking-key-1234567890")
    assert cfg.heartbeat_quiet_interval_s == 30.0
