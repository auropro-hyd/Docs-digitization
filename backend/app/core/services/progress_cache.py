"""In-process latest-progress cache for the polling fallback path.

The WebSocket notification adapter is the primary delivery channel
for OCR progress (heartbeat ticks from
:mod:`app.workflow.nodes`). Real users sometimes can't hold a WS
open: corporate proxies, captive portals, mobile carriers that strip
the ``Upgrade`` header, or just a tab that lost focus and got its
WS killed. Without a fallback those users see "Loading..." until
the run completes.

This module solves that with the smallest moving piece that works:
a bounded LRU dict keyed by channel (``doc_id`` for the document
pipeline, ``run_id`` later for BMR runs). The notification adapter
writes the latest payload here on every broadcast; an HTTP route
reads it on a slow poll. The cache has no awareness of run
lifecycle — it just holds the last thing emitted, with TTL eviction
so a long-idle entry doesn't keep memory pinned forever.

Sized for a single-tenant pilot deployment: 512 entries and a
30-minute TTL covers the longest realistic OCR run with margin.
Bump both via env if a larger deployment ever needs it.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from typing import Any


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


_DEFAULT_MAX_ENTRIES = _env_int("AT_PROGRESS_CACHE__MAX_ENTRIES", 512)
_DEFAULT_TTL_SECONDS = _env_int("AT_PROGRESS_CACHE__TTL_SECONDS", 1800)


class ProgressCache:
    """Bounded LRU + TTL store of the latest progress payload per channel.

    Thread-safe via a single lock — payloads are tiny dicts and the
    write side is the WS broadcast (off the hot OCR path), so the
    contention cost is negligible compared to the I/O it sits next
    to. The lock also protects the LRU eviction order, which the
    GIL alone wouldn't.
    """

    def __init__(
        self,
        *,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._monotonic = monotonic
        self._entries: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._lock = threading.Lock()

    def set(self, channel: str, payload: dict) -> None:
        """Record the latest payload for ``channel``.

        Only ``type=="progress"`` payloads are kept — the polling
        fallback's contract is "tell me where the engine got to,"
        not "replay every status change." Filtering at the cache
        boundary keeps the consumer simple and avoids storing the
        much-noisier status / page_update / quality_gate payloads.
        """

        if not isinstance(payload, dict) or payload.get("type") != "progress":
            return
        with self._lock:
            now = self._monotonic()
            # Move to end (most recent) — OrderedDict preserves
            # insertion order so the eviction sweep walks oldest-first.
            self._entries[channel] = (now, payload)
            self._entries.move_to_end(channel)
            self._evict_if_needed(now)

    def get(self, channel: str) -> dict | None:
        """Return the most recent payload for ``channel`` or ``None``.

        TTL is enforced lazily on read so a slow poll doesn't surface
        stale data left over from a run that finished hours ago.
        """

        with self._lock:
            entry = self._entries.get(channel)
            if entry is None:
                return None
            ts, payload = entry
            if self._monotonic() - ts > self._ttl_seconds:
                self._entries.pop(channel, None)
                return None
            return payload

    def clear(self, channel: str | None = None) -> None:
        """Drop one channel (e.g. when a run completes) or everything."""

        with self._lock:
            if channel is None:
                self._entries.clear()
            else:
                self._entries.pop(channel, None)

    def _evict_if_needed(self, now: float) -> None:
        # Cheap TTL sweep at write time so we don't need a background
        # task. Bounded by max_entries so it stays O(max_entries) worst
        # case, which the LRU cap keeps small.
        expired = [
            ch for ch, (ts, _) in self._entries.items() if now - ts > self._ttl_seconds
        ]
        for ch in expired:
            self._entries.pop(ch, None)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


_singleton: ProgressCache | None = None
_singleton_lock = threading.Lock()


def get_progress_cache() -> ProgressCache:
    """Return the process-wide cache singleton.

    Lazily constructed so tests can monkeypatch
    :data:`_DEFAULT_MAX_ENTRIES` / :data:`_DEFAULT_TTL_SECONDS` via
    env before the first import touches the singleton.
    """

    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ProgressCache()
    return _singleton


def _reset_for_tests() -> None:
    """Drop the singleton so tests can install a clean cache."""

    global _singleton
    with _singleton_lock:
        _singleton = None


__all__ = ["ProgressCache", "get_progress_cache"]
