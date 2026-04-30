"""When compliance is cancelled mid-flight, the UI must hear about it.

Discovered from a real run: the user POSTed ``/api/compliance/<id>/run``
and 8 seconds later uvicorn ``--reload`` killed the worker process
because a pytest test file was rewritten. The compliance task got
cancelled by the lifespan shutdown, the cancel handler logged
server-side, and **no signal reached the frontend**. From the user's
perspective the audit silently never happened.

The fix this module pins: the ``except asyncio.CancelledError`` branch
broadcasts a ``compliance_progress`` payload with ``phase=cancelled``
and an actionable label, mirroring the existing ``error`` branch's
shape so the dashboard can render an "interrupted, please re-run"
banner instead of a permanent spinner. The exception is then
re-raised so asyncio's task-cancellation contract is honoured —
without that, callers awaiting the task see a normal return that
masks the cancel.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.api.routes.compliance import _run_compliance_pipeline


def _seed_doc(tmp_path: Path, doc_id: str) -> Path:
    """Drop the minimal ``result.json`` the pipeline needs to start."""

    doc_dir = tmp_path / doc_id
    doc_dir.mkdir(parents=True)
    (doc_dir / "result.json").write_text(
        json.dumps({
            "filename": "test.pdf",
            "total_pages": 1,
            "extractions": [{"page_num": 1, "markdown": "# Test"}],
            "key_value_pairs": [],
        }),
        encoding="utf-8",
    )
    # The lock file the route writes before spawning the task —
    # required so the ``finally`` cleanup has something to unlink.
    (doc_dir / "compliance_running.lock").write_text("running", encoding="utf-8")
    return doc_dir


@pytest.mark.asyncio
async def test_cancelled_pipeline_broadcasts_actionable_message_to_ws(
    tmp_path: Path,
) -> None:
    doc_id = "cancel-test-doc"
    doc_dir = _seed_doc(tmp_path, doc_id)

    captured_broadcasts: list[dict] = []

    async def fake_broadcast(channel: str, data: dict) -> None:
        captured_broadcasts.append({"channel": channel, **data})

    # Mock the inner pipeline to raise CancelledError immediately,
    # simulating the uvicorn-reload-during-run scenario.
    async def cancelled_pipeline(**_kwargs):
        raise asyncio.CancelledError()

    with patch(
        "app.workflow.compliance_graph.run_compliance_pipeline",
        side_effect=cancelled_pipeline,
    ), patch(
        "app.api.websocket.manager.broadcast",
        side_effect=fake_broadcast,
    ):
        # The cancel handler MUST re-raise CancelledError so asyncio
        # task semantics aren't masked. Catching it here at the test
        # boundary is what asyncio.gather/await does in production.
        with pytest.raises(asyncio.CancelledError):
            await _run_compliance_pipeline(doc_id, doc_dir, [])

    assert captured_broadcasts, (
        "compliance cancellation must reach the WS so the UI can show "
        "an interrupted state — silence here is exactly the symptom "
        "the user reported"
    )
    payload = captured_broadcasts[-1]
    assert payload["channel"] == doc_id
    assert payload["type"] == "compliance_progress"
    assert payload["phase"] == "cancelled"
    assert payload["status"] == "cancelled"
    assert "interrupted" in payload["label"].lower()


@pytest.mark.asyncio
async def test_cancelled_pipeline_still_clears_lock_file(tmp_path: Path) -> None:
    """The lock cleanup in ``finally`` must run even when the cancel
    handler re-raises — otherwise a re-run of the same doc gets a
    409 "already running" until the stale-lock TTL expires."""

    doc_id = "lock-cleanup-test"
    doc_dir = _seed_doc(tmp_path, doc_id)
    lock_path = doc_dir / "compliance_running.lock"
    assert lock_path.exists()

    async def cancelled(**_kwargs):
        raise asyncio.CancelledError()

    async def noop_broadcast(*_args, **_kwargs):
        pass

    with patch(
        "app.workflow.compliance_graph.run_compliance_pipeline",
        side_effect=cancelled,
    ), patch(
        "app.api.websocket.manager.broadcast",
        side_effect=noop_broadcast,
    ):
        with pytest.raises(asyncio.CancelledError):
            await _run_compliance_pipeline(doc_id, doc_dir, [])

    assert not lock_path.exists(), (
        "lock file must be removed in the ``finally`` block even when "
        "the cancel handler re-raises — otherwise re-running the same "
        "doc gets a stale 409"
    )


@pytest.mark.asyncio
async def test_failed_broadcast_does_not_swallow_cancel(tmp_path: Path) -> None:
    """If the WS broadcast itself fails (likely during a reload — the
    socket may already be in teardown), the cancel must still be
    re-raised. Otherwise a swallowed cancel hangs the lifespan
    shutdown waiting for a task that's "complete" but really
    cancelled."""

    doc_id = "broken-ws-test"
    doc_dir = _seed_doc(tmp_path, doc_id)

    async def cancelled(**_kwargs):
        raise asyncio.CancelledError()

    async def broken_broadcast(*_args, **_kwargs):
        raise ConnectionError("WS already closed")

    with patch(
        "app.workflow.compliance_graph.run_compliance_pipeline",
        side_effect=cancelled,
    ), patch(
        "app.api.websocket.manager.broadcast",
        side_effect=broken_broadcast,
    ):
        with pytest.raises(asyncio.CancelledError):
            await _run_compliance_pipeline(doc_id, doc_dir, [])
