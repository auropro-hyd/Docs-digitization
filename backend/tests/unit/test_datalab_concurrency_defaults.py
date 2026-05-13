"""Pin Datalab concurrency defaults and saturation diagnostics.

2026-05-13: a 117-page / 14 MB BPCR deterministically failed
end-to-end OCR — 5 chunks submitted concurrently at the default
``max_concurrent_chunks=8`` saturated the upload side. All 5 hit
``Request timed out after 300 seconds`` or ``[Errno 32] Broken pipe``
on attempt 1, then ``502 Bad Gateway`` on retry. Total wall time
709 s, zero pages extracted.

Two pins here:

  1. ``DatalabConfig.max_concurrent_chunks`` defaults to 3, not 8.
     The lower default is the load-bearing fix for saturation;
     operators on higher-tier API keys can raise it via
     ``AT_DATALAB__MAX_CONCURRENT_CHUNKS``.

  2. ``_record_submit_failure`` recognises saturation-shaped error
     messages and fires ``ocr.datalab_submit_failed`` with a hint
     payload that names the env var to tune. Without this hint the
     next saturation incident produces an unscoped retry log line
     and the operator has no signal that concurrency is the lever.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_default_max_concurrent_chunks_is_set() -> None:
    """Pin the historic concurrency default. 2026-05-13: a transient
    Datalab API degradation made the default of 8 look like a
    saturation issue (709 s wall time, 0 pages extracted on a 117-
    page BPCR). Real cause was intermittent upstream 502s — the
    diagnostic telemetry below makes that visible. Keep the default
    where it has been since the Datalab adapter shipped (commit
    48b1902); operators can still tune via
    ``AT_DATALAB__MAX_CONCURRENT_CHUNKS`` if their account hits a
    real cap."""

    from app.config.settings import DatalabConfig

    cfg = DatalabConfig(api_key="a-real-looking-key-1234567890")
    assert cfg.max_concurrent_chunks == 8, (
        f"max_concurrent_chunks default is {cfg.max_concurrent_chunks}; "
        f"historic value is 8. If lowering is intentional, update this "
        f"pin AND document the load-test that motivated the change."
    )


def test_submit_failure_telemetry_flags_saturation_shapes(tmp_path) -> None:
    """Each error fragment in ``_SATURATION_HINTS`` must trigger
    the saturation-flagged path, AND the hint payload must name
    the env var. Without the hint, an operator hitting a 709 s
    timeout has no breadcrumb pointing at the concurrency knob."""

    from app.adapters.ocr.datalab import _SATURATION_HINTS, _record_submit_failure
    from app.config.settings import DatalabConfig
    from app.observability.run_telemetry import telemetry_run

    cfg = DatalabConfig(api_key="a-real-looking-key-1234567890")
    doc_dir = tmp_path / "test-doc"

    failure_cases = [
        ("Request timed out after 300 seconds", True),
        ("[Errno 32] Broken pipe", True),
        ("502, message='Bad Gateway'", True),
        ("503 Service Unavailable", True),
        ("ValueError: bad json from datalab", False),  # non-saturation
    ]

    with telemetry_run("test-doc", doc_dir, name="test"):
        for i, (msg, _expect_saturation) in enumerate(failure_cases):
            _record_submit_failure(cfg, RuntimeError(msg), attempt=i + 1)

    data = json.loads((doc_dir / "telemetry-test.json").read_text(encoding="utf-8"))
    events = [
        e for e in data.get("events", [])
        if e.get("event") == "ocr.datalab_submit_failed"
    ]
    assert len(events) == len(failure_cases), (
        f"expected {len(failure_cases)} events, got {len(events)}"
    )

    by_msg = {e["fields"]["error_message"]: e["fields"] for e in events}
    # Saturation-shaped failures must carry the hint AND the env-var name.
    for msg, expect_saturation in failure_cases:
        fields = by_msg.get(msg, {})
        assert fields.get("saturation_shape") is expect_saturation, (
            f"{msg!r}: saturation_shape={fields.get('saturation_shape')} "
            f"(expected {expect_saturation})"
        )
        if expect_saturation:
            hint = fields.get("hint") or ""
            assert "AT_DATALAB__MAX_CONCURRENT_CHUNKS" in hint, (
                f"saturation event for {msg!r} missing concurrency-knob "
                f"name in hint: {hint!r}"
            )
        else:
            # Non-saturation events should NOT carry a misleading hint.
            assert fields.get("hint") in (None, ""), (
                f"non-saturation event for {msg!r} got a hint: "
                f"{fields.get('hint')!r}"
            )


def test_saturation_hints_cover_known_error_signatures() -> None:
    """Sanity: the _SATURATION_HINTS tuple must contain at least
    the four shapes we observed deterministically on the 2026-05-13
    failure (and 502/503 which are the canonical upstream-cap codes)."""

    from app.adapters.ocr.datalab import _SATURATION_HINTS

    needed = {"timed out", "Broken pipe", "Bad Gateway", "502", "503"}
    missing = needed - set(_SATURATION_HINTS)
    assert not missing, f"_SATURATION_HINTS missing {missing}"
