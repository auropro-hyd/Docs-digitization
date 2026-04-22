"""Tests for the in-process :class:`EventBus`.

The bus is the v0 transport for Spec 004 follow-up #6 — live run
events streamed over WebSocket. Downstream adapters (Redis, NATS,
etc.) swap in without changing the publisher contract, so these
tests lock the invariants the adapter must preserve.
"""

from __future__ import annotations

import asyncio

import pytest

from app.bmr.events import EventBus


@pytest.mark.asyncio
async def test_publish_delivers_to_subscriber():
    bus = EventBus()
    q = bus.subscribe("run-1")
    bus.publish("run.started", "run-1", {"package_id": "pkg-1"})
    envelope = await asyncio.wait_for(q.get(), timeout=1.0)
    assert envelope["event"] == "run.started"
    assert envelope["run_id"] == "run-1"
    assert envelope["payload"] == {"package_id": "pkg-1"}
    assert envelope["schema_version"] == "1.0"
    assert "timestamp" in envelope


@pytest.mark.asyncio
async def test_publish_is_scoped_to_run_id():
    bus = EventBus()
    q_a = bus.subscribe("run-a")
    q_b = bus.subscribe("run-b")
    bus.publish("run.started", "run-a", {})
    envelope = await asyncio.wait_for(q_a.get(), timeout=1.0)
    assert envelope["run_id"] == "run-a"
    assert q_b.empty()


@pytest.mark.asyncio
async def test_multiple_subscribers_all_receive():
    bus = EventBus()
    q1 = bus.subscribe("run-1")
    q2 = bus.subscribe("run-1")
    bus.publish("run.completed", "run-1", {"rules_evaluated": 3})
    e1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    e2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert e1["event"] == e2["event"] == "run.completed"


@pytest.mark.asyncio
async def test_unsubscribe_drops_queue():
    bus = EventBus()
    q = bus.subscribe("run-1")
    assert bus.subscriber_count("run-1") == 1
    bus.unsubscribe("run-1", q)
    assert bus.subscriber_count("run-1") == 0
    bus.publish("run.started", "run-1", {})
    assert q.empty()


@pytest.mark.asyncio
async def test_publish_from_background_thread():
    """Publishers outside the event loop still reach subscribers.

    The BMR pipeline runs synchronously on a worker thread; the bus
    must marshal events back to the subscriber's loop.
    """

    bus = EventBus()
    q = bus.subscribe("run-1")

    def _emit() -> None:
        bus.publish("run.started", "run-1", {"src": "worker"})

    await asyncio.to_thread(_emit)
    envelope = await asyncio.wait_for(q.get(), timeout=1.0)
    assert envelope["payload"] == {"src": "worker"}


@pytest.mark.asyncio
async def test_publish_with_no_subscribers_is_silent():
    bus = EventBus()
    # Must not raise even when no one is listening.
    bus.publish("run.completed", "no-one", {})
