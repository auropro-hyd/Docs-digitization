"""Tests for the per-stage progress events published by ``_observe_stage``.

Without these events the ``/api/bmr/runs/{run_id}/events`` WebSocket
only ever surfaced the run-level lifecycle (``run.started``,
``run.completed``, …). A subscriber on the run-detail page got a
spinner that didn't move until the whole pipeline finished. The
events introduced here let a UI render a 5-stage progress bar with
near-realtime granularity.

The invariants this module pins:

- every wrapped stage call emits both ``bmr.stage.entered`` and
  ``bmr.stage.completed`` (success path),
- both events carry ``stage`` / ``stage_index`` / ``total_stages``
  so a UI can render percent without re-deriving the topology,
- ``stage.completed`` carries ``duration_ms`` and ``status`` (the
  stage's own outcome, lifted from the result dict),
- a stage call with no ``run_id`` in state degrades silently —
  observability never breaks the pipeline (Constitution VI).
"""

from __future__ import annotations

import asyncio

import pytest

from app.bmr.events import get_event_bus, reset_event_bus
from app.bmr.workflow.models import RunStage, RunStatus
from app.bmr.workflow.stages import (
    _STAGE_ORDER,
    _observe_stage,
    _stage_position,
)


@pytest.fixture(autouse=True)
def _clean_bus():
    reset_event_bus()
    yield
    reset_event_bus()


@pytest.mark.asyncio
async def test_stage_position_is_one_indexed_and_covers_all_stages() -> None:
    """The position contract is what the UI's percent calc relies on —
    pin it so a renaming of the enum can't silently shift indices."""

    assert _stage_position(RunStage.INGEST) == 1
    assert _stage_position(RunStage.LEGIBILITY_AND_CLASSIFICATION) == 2
    assert _stage_position(RunStage.EXTRACTION) == 3
    assert _stage_position(RunStage.COMPLIANCE) == 4
    assert _stage_position(RunStage.REPORT) == 5
    assert len(_STAGE_ORDER) == 5


@pytest.mark.asyncio
async def test_observe_stage_emits_entered_and_completed_with_topology() -> None:
    """Both events fire on the success path with the topology fields a
    UI subscriber needs (stage / stage_index / total_stages)."""

    bus = get_event_bus()
    queue = bus.subscribe("run-x")

    def fake_stage(state):
        return {"stage": RunStage.EXTRACTION, "status": RunStatus.RUNNING}

    wrapped = _observe_stage(RunStage.EXTRACTION, fake_stage)
    wrapped({"run_id": "run-x"})

    entered = await asyncio.wait_for(queue.get(), timeout=1.0)
    completed = await asyncio.wait_for(queue.get(), timeout=1.0)

    assert entered["event"] == "bmr.stage.entered"
    assert entered["payload"]["stage"] == "extraction"
    assert entered["payload"]["stage_index"] == 3
    assert entered["payload"]["total_stages"] == 5

    assert completed["event"] == "bmr.stage.completed"
    assert completed["payload"]["stage"] == "extraction"
    assert completed["payload"]["stage_index"] == 3
    assert "duration_ms" in completed["payload"]
    # ``status`` is lifted from the stage result so a failure surfaces
    # immediately on this event rather than waiting for ``run.failed``.
    assert completed["payload"]["status"] == "running"


@pytest.mark.asyncio
async def test_observe_stage_skips_publish_when_run_id_missing() -> None:
    """A stage invoked with no ``run_id`` (defensive path used by some
    test harnesses) must not publish events targeted at an unknown
    channel — better to drop than synthesise a fake id."""

    bus = get_event_bus()
    queue = bus.subscribe("run-y")

    def fake_stage(state):
        return {"stage": RunStage.INGEST}

    wrapped = _observe_stage(RunStage.INGEST, fake_stage)
    wrapped({})  # no run_id

    # Let any pending threadsafe puts actually land before we assert
    # the queue is empty — without this, ``empty()`` would pass even
    # if a publish *was* scheduled.
    await asyncio.sleep(0)
    assert queue.empty()


@pytest.mark.asyncio
async def test_observe_stage_emits_completed_even_when_stage_raises() -> None:
    """A stage that raises must still publish ``stage.completed`` so the
    UI doesn't sit on the entered-but-not-completed half-state. The
    payload's ``status`` will be ``None`` (no result dict to lift from)
    which the UI treats as "ended in error, see run.failed"."""

    bus = get_event_bus()
    queue = bus.subscribe("run-z")

    def boom(state):
        raise RuntimeError("synthetic stage failure")

    wrapped = _observe_stage(RunStage.COMPLIANCE, boom)
    with pytest.raises(RuntimeError, match="synthetic"):
        wrapped({"run_id": "run-z"})

    entered = await asyncio.wait_for(queue.get(), timeout=1.0)
    completed = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert entered["event"] == "bmr.stage.entered"
    assert completed["event"] == "bmr.stage.completed"
    assert completed["payload"]["status"] is None
