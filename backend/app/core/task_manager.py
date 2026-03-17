"""Centralized async task lifecycle manager.

Tracks all background ``asyncio.Task`` instances so they can be:
  - queried  (``is_running``, ``list_tasks``)
  - cancelled individually (``cancel``)
  - drained on application shutdown (``shutdown``)

Usage::

    from app.core.task_manager import task_manager

    task_manager.spawn("compliance:abc-123", my_coro())
    task_manager.cancel("compliance:abc-123")

    # In FastAPI lifespan shutdown:
    await task_manager.shutdown(timeout=10)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)


class TaskManager:
    """Process-wide registry of cancellable background tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    # ── spawn / cancel ────────────────────────────────────────

    def spawn(
        self,
        key: str,
        coro: Coroutine[Any, Any, Any],
        *,
        replace: bool = False,
    ) -> asyncio.Task[Any]:
        """Create a tracked ``asyncio.Task`` for *coro*.

        Args:
            key: Unique identifier (e.g. ``"compliance:<doc_id>"``).
            coro: The coroutine to schedule.
            replace: If *True* and a task with the same key is already running,
                     cancel it first.  Otherwise raise ``RuntimeError``.

        Returns:
            The newly created task.
        """
        existing = self._tasks.get(key)
        if existing and not existing.done():
            if replace:
                existing.cancel()
                logger.info("Replaced running task %s", key)
            else:
                coro.close()
                raise RuntimeError(f"Task {key!r} is already running")

        task = asyncio.create_task(coro, name=key)
        self._tasks[key] = task
        task.add_done_callback(lambda _t: self._tasks.pop(key, None))
        logger.debug("Spawned task %s", key)
        return task

    def cancel(self, key: str) -> bool:
        """Cancel a running task. Returns *True* if a task was actually cancelled."""
        task = self._tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
            logger.info("Cancelled task %s", key)
            return True
        return False

    # ── query ─────────────────────────────────────────────────

    def is_running(self, key: str) -> bool:
        task = self._tasks.get(key)
        return task is not None and not task.done()

    def list_tasks(self) -> dict[str, str]:
        """Return ``{key: state}`` for every tracked task."""
        return {
            key: ("running" if not t.done() else "done")
            for key, t in self._tasks.items()
        }

    @property
    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())

    # ── shutdown ──────────────────────────────────────────────

    async def shutdown(self, timeout: float = 10.0) -> None:
        """Cancel every tracked task and wait for them to finish.

        Called from the FastAPI lifespan shutdown hook so that
        ``Ctrl-C`` / ``SIGTERM`` kills all in-flight work immediately.
        """
        running = {k: t for k, t in self._tasks.items() if not t.done()}
        if not running:
            return

        logger.info("Shutting down %d background task(s)…", len(running))
        for key, task in running.items():
            task.cancel()
            logger.info("  → cancelled %s", key)

        results = await asyncio.gather(*running.values(), return_exceptions=True)
        for key, result in zip(running, results, strict=False):
            if isinstance(result, asyncio.CancelledError):
                logger.debug("  ✓ %s cancelled cleanly", key)
            elif isinstance(result, Exception):
                logger.warning("  ✗ %s raised during cancel: %s", key, result)

        self._tasks.clear()
        logger.info("All background tasks shut down")


task_manager = TaskManager()
