"""In-process pub/sub for BMR run lifecycle events (Spec 004 follow-up #6).

The :class:`EventBus` is a thin, thread-safe publisher:

- Sync callers (e.g. the LangGraph pipeline, the HITL service) publish
  events via :meth:`EventBus.publish`.
- Async WebSocket handlers subscribe via :meth:`EventBus.subscribe` to
  receive a per-connection :class:`asyncio.Queue`.
- The bus marshals events onto the subscriber's event loop with
  ``loop.call_soon_threadsafe`` so publishers never have to care about
  which loop the subscriber lives on.

The bus is intentionally in-process; a Redis/NATS adapter can be swapped
in later without changing callers.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_EVENT_SCHEMA_VERSION = "1.0"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[
            str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]
        ] = {}
        self._lock = threading.Lock()

    def subscribe(self, run_id: str) -> asyncio.Queue:
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._subs.setdefault(run_id, set()).add((loop, q))
        return q

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            subs = self._subs.get(run_id)
            if subs is None:
                return
            to_drop = {entry for entry in subs if entry[1] is queue}
            subs -= to_drop
            if not subs:
                self._subs.pop(run_id, None)

    def subscriber_count(self, run_id: str) -> int:
        with self._lock:
            return len(self._subs.get(run_id, set()))

    def publish(
        self,
        event: str,
        run_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        # Observability: attach the current trace id to the envelope so
        # WebSocket subscribers can correlate events with server-side
        # logs. Fail-open — if the observability layer is absent we still
        # ship the event.
        trace_id: str | None = None
        span_id: str | None = None
        try:
            from app.observability import current_trace

            ctx = current_trace()
            if ctx is not None:
                trace_id = ctx.trace_id
                span_id = ctx.span_id
        except Exception:  # pragma: no cover — fail-open
            pass

        envelope = {
            "schema_version": _EVENT_SCHEMA_VERSION,
            "event": event,
            "run_id": run_id,
            "timestamp": _now_iso(),
            "trace_id": trace_id,
            "span_id": span_id,
            "payload": dict(payload or {}),
        }
        # Hold the lock across scheduling: if we release it, a subscriber
        # can unsubscribe + close its loop before call_soon_threadsafe
        # fires, and the RuntimeError branch below silently drops the
        # event with no record in the audit trail. Scheduling
        # call_soon_threadsafe is cheap (does not block on queue fill),
        # so holding the lock for the whole loop is acceptable.
        with self._lock:
            subs = list(self._subs.get(run_id, set()))
            for loop, q in subs:
                try:
                    loop.call_soon_threadsafe(_put_nowait_silent, q, envelope)
                except RuntimeError:
                    logger.warning(
                        "dropping %s event for run %s: subscriber loop is closed",
                        event,
                        run_id,
                    )
                    continue


def _put_nowait_silent(q: asyncio.Queue, envelope: dict[str, Any]) -> None:
    try:
        q.put_nowait(envelope)
    except asyncio.QueueFull:
        # Drop oldest event so slow subscribers don't freeze publishers.
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            q.put_nowait(envelope)
        except asyncio.QueueFull:
            pass


_default_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return a process-wide default :class:`EventBus`.

    Tests can bypass this by constructing their own :class:`EventBus` and
    wiring it through the service constructors directly.
    """

    global _default_bus
    if _default_bus is None:
        _default_bus = EventBus()
    return _default_bus


def reset_event_bus() -> None:
    """Used by tests to drop global state between runs."""

    global _default_bus
    _default_bus = None


__all__ = ["EventBus", "get_event_bus", "reset_event_bus"]
