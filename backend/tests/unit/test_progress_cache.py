"""Tests for the latest-progress cache that drives the polling fallback.

The cache sits between the notification adapter (writer) and the
``GET /api/documents/{doc_id}/progress`` route (reader). The
invariants this module pins:

- only ``type=="progress"`` payloads are stored (status / page_update
  / quality_gate noise must not pollute the polling reply),
- the reader sees the most recent payload (LRU semantics),
- entries past TTL are not returned (lazy expiry on read),
- the cap is honoured so a long-lived deployment with churn doesn't
  leak unbounded memory.
"""

from __future__ import annotations

from app.core.services.progress_cache import ProgressCache


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_only_progress_payloads_are_stored() -> None:
    """Status / page_update / quality_gate writes must be ignored.

    The polling fallback's contract is "tell me how OCR is doing,"
    not "replay every status change." Silently dropping non-progress
    payloads at the cache boundary keeps the consumer's logic
    simple and prevents a slow poll from surfacing a stale status
    label as if it were progress.
    """

    cache = ProgressCache(max_entries=10, ttl_seconds=60, monotonic=_FakeClock())

    cache.set("doc-a", {"type": "status", "status": "azure_di_running"})
    cache.set("doc-a", {"type": "page_update", "page_num": 3})
    assert cache.get("doc-a") is None

    cache.set("doc-a", {"type": "progress", "percent": 30, "label": "x"})
    out = cache.get("doc-a")
    assert out is not None and out["percent"] == 30


def test_set_overwrites_with_latest_payload() -> None:
    cache = ProgressCache(max_entries=10, ttl_seconds=60, monotonic=_FakeClock())

    cache.set("doc-a", {"type": "progress", "percent": 10, "label": "first"})
    cache.set("doc-a", {"type": "progress", "percent": 80, "label": "later"})

    out = cache.get("doc-a")
    assert out is not None
    assert out["percent"] == 80
    assert out["label"] == "later"


def test_ttl_evicts_on_read() -> None:
    """A reader that polls hours after a run finished must not see
    stale data — better an empty response than a confusing one."""

    clock = _FakeClock()
    cache = ProgressCache(max_entries=10, ttl_seconds=10, monotonic=clock)

    cache.set("doc-a", {"type": "progress", "percent": 50, "label": "x"})

    clock.advance(5)
    assert cache.get("doc-a") is not None

    clock.advance(20)  # well past TTL
    assert cache.get("doc-a") is None


def test_max_entries_bounds_memory() -> None:
    """Deployments with high doc churn must not leak entries forever."""

    cache = ProgressCache(max_entries=2, ttl_seconds=60, monotonic=_FakeClock())

    cache.set("doc-a", {"type": "progress", "percent": 10, "label": "a"})
    cache.set("doc-b", {"type": "progress", "percent": 20, "label": "b"})
    cache.set("doc-c", {"type": "progress", "percent": 30, "label": "c"})

    # ``doc-a`` was the LRU entry; the third write evicted it.
    assert cache.get("doc-a") is None
    assert cache.get("doc-b") is not None
    assert cache.get("doc-c") is not None


def test_set_refreshes_lru_position() -> None:
    """A re-write of an existing key must move it to the MRU position."""

    cache = ProgressCache(max_entries=2, ttl_seconds=60, monotonic=_FakeClock())

    cache.set("doc-a", {"type": "progress", "percent": 10, "label": "a"})
    cache.set("doc-b", {"type": "progress", "percent": 20, "label": "b"})

    # Refresh doc-a — now doc-b is the LRU entry.
    cache.set("doc-a", {"type": "progress", "percent": 11, "label": "a2"})

    cache.set("doc-c", {"type": "progress", "percent": 30, "label": "c"})

    assert cache.get("doc-a") is not None  # survived
    assert cache.get("doc-b") is None       # evicted
    assert cache.get("doc-c") is not None


def test_clear_drops_one_or_all() -> None:
    cache = ProgressCache(max_entries=10, ttl_seconds=60, monotonic=_FakeClock())

    cache.set("doc-a", {"type": "progress", "percent": 10, "label": "a"})
    cache.set("doc-b", {"type": "progress", "percent": 20, "label": "b"})

    cache.clear("doc-a")
    assert cache.get("doc-a") is None
    assert cache.get("doc-b") is not None

    cache.clear()
    assert cache.get("doc-b") is None
