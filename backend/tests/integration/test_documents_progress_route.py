"""HTTP integration test for the polling-fallback progress route.

Covers the full path the polling fallback exercises:

  WS broadcast → WebSocketNotifyAdapter.send_update → ProgressCache.set
                                                           ▼
  GET /api/documents/{doc_id}/progress ← ProgressCache.get

This is intentionally an integration test (real FastAPI app, real
notification adapter) so a future refactor that decouples the cache
from the adapter — or moves the route — gets caught here.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.adapters.notification.websocket import WebSocketNotifyAdapter
from app.core.services.progress_cache import (
    _reset_for_tests,
    get_progress_cache,
)
from app.main import create_app


@pytest.fixture(autouse=True)
def _clean_cache():
    """Reset the singleton between tests so cross-test pollution can't
    mask a real regression in the cache key/lookup logic."""

    _reset_for_tests()
    yield
    _reset_for_tests()


def test_progress_route_returns_default_when_no_data() -> None:
    """A fresh upload that hasn't seen any OCR ticks yet must not
    404 — the frontend polls speculatively and a 404 would surface
    as an error rather than a "still warming up" state."""

    with TestClient(create_app()) as c:
        resp = c.get("/api/documents/never-seen/progress")
        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "progress"
        assert body["percent"] == 0
        assert body["phase"] is None


@pytest.mark.asyncio
async def test_progress_route_returns_latest_payload_after_ws_broadcast() -> None:
    """The most recent WS broadcast for a doc_id must be readable via
    the HTTP poll, with the same shape the frontend already understands.
    """

    adapter = WebSocketNotifyAdapter()
    payload = {
        "type": "progress",
        "status": "azure_di_running",
        "phase": "analyzing",
        "percent": 47,
        "label": "Chunk 2/4 (pages 11-20) — analyzing (12s)",
    }
    await adapter.send_update("doc-poll-test", payload)

    # Sanity: the cache layer captured it.
    assert get_progress_cache().get("doc-poll-test") == payload

    with TestClient(create_app()) as c:
        resp = c.get("/api/documents/doc-poll-test/progress")
    assert resp.status_code == 200
    body = resp.json()
    # All five reader-relevant fields round-trip cleanly.
    assert body["percent"] == 47
    assert body["phase"] == "analyzing"
    assert body["label"].startswith("Chunk 2/4")


@pytest.mark.asyncio
async def test_non_progress_payloads_are_not_cached() -> None:
    """Status / page_update broadcasts must not surface through the
    progress route — the cache filters them out at write time so a
    polling client never sees a stale status label dressed up as
    progress."""

    adapter = WebSocketNotifyAdapter()
    await adapter.send_update("doc-x", {"type": "status", "status": "ingested"})
    await adapter.send_update("doc-x", {"type": "page_update", "page_num": 3})

    with TestClient(create_app()) as c:
        resp = c.get("/api/documents/doc-x/progress")
    assert resp.status_code == 200
    assert resp.json()["percent"] == 0  # default, not the status payload
