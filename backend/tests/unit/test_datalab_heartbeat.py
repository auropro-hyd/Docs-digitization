"""Tests for the Datalab adapter's heartbeat-during-poll behaviour.

The SDK's ``client.convert()`` blocks until the request reaches
``status=complete`` and provides no per-poll callback. Without our
heartbeat wrapper, multi-page chunks looked frozen for tens of
seconds. This test pins that we tick the callback at every poll
interval while ``convert`` is in flight, and that the percent stays
pinned to the baseline (heartbeats are *labels*, not bar movements).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.adapters.ocr.datalab import DatalabOCRAdapter
from app.config.settings import DatalabConfig


class _SlowConvertClient:
    """Stand-in for ``AsyncDatalabClient`` that blocks for ``finish_after``
    seconds and records the moment ``convert`` was called."""

    def __init__(self, *, finish_after: float) -> None:
        self.finish_after = finish_after
        self.called = False

    async def convert(self, *, file_path, options, max_polls, poll_interval):
        self.called = True
        await asyncio.sleep(self.finish_after)

        class _Result:
            success = True
            checkpoint_id = None
            parse_quality_score = None
        return _Result()


@pytest.mark.asyncio
async def test_heartbeat_ticks_while_convert_blocks() -> None:
    """While the SDK polls inside ``convert``, the adapter must emit
    one tick per ``poll_interval`` seconds. The percent stays at the
    baseline so the bar doesn't snap backwards mid-chunk; the label
    moves forward with elapsed time so the user sees activity.
    """

    cfg = DatalabConfig(
        api_key="test",
        max_polls=100,
        poll_interval=1,
        submit_max_retries=1,
        submit_retry_base_delay=0.1,
        chunk_pages=10,
        max_concurrent_chunks=1,
    )
    adapter = DatalabOCRAdapter(cfg)
    client = _SlowConvertClient(finish_after=2.5)

    ticks: list[tuple[int, str]] = []

    def callback(percent: int, label: str) -> None:
        ticks.append((percent, label))

    await adapter._convert_with_heartbeat(
        client=client,
        pdf_path="/tmp/fake.pdf",
        opts=None,
        on_tick=lambda elapsed: callback(33, f"chunk x — analyzing ({elapsed:.0f}s)"),
    )

    # 2.5s of work / 1s poll interval = at least 2 heartbeats.
    elapsed_labels = [t[1] for t in ticks if "analyzing" in t[1]]
    assert len(elapsed_labels) >= 2, (
        f"expected ≥2 heartbeats during 2.5s convert, got {elapsed_labels}"
    )
    assert all(t[0] == 33 for t in ticks), (
        "heartbeats must hold the baseline percent — only chunk completion "
        f"moves the bar forward; got {ticks}"
    )

    # Each successive heartbeat reports a larger elapsed time.
    elapsed_seconds = [
        float(label.rsplit("(", 1)[1].rstrip("s)")) for label in elapsed_labels
    ]
    assert elapsed_seconds == sorted(elapsed_seconds)


@pytest.mark.asyncio
async def test_heartbeat_does_not_fire_when_convert_finishes_within_one_poll() -> None:
    """A fast chunk shouldn't emit any heartbeats — only the chunk-complete
    progress entry (handled by the caller, not us). This guards against
    a flooded callback on small documents."""

    cfg = DatalabConfig(
        api_key="test",
        max_polls=100,
        poll_interval=1,
        submit_max_retries=1,
        submit_retry_base_delay=0.1,
        chunk_pages=10,
        max_concurrent_chunks=1,
    )
    adapter = DatalabOCRAdapter(cfg)
    client = _SlowConvertClient(finish_after=0.05)  # well under the 1s poll

    ticks: list[Any] = []
    await adapter._convert_with_heartbeat(
        client=client,
        pdf_path="/tmp/fake.pdf",
        opts=None,
        on_tick=lambda elapsed: ticks.append(elapsed),
    )

    assert ticks == [], (
        "heartbeat must not fire when the convert() call returns inside the "
        "first poll window — small documents would otherwise emit confusing "
        "labels"
    )
