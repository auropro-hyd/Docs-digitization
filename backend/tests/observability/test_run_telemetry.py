"""Pin the per-run telemetry sink contract.

The load-bearing invariants:

1. **Isolation** — concurrent runs never see each other's events.
2. **Auto-capture** — every ``logger.*()`` call inside a
   ``telemetry_run`` block lands in the sink, with no per-step
   instrumentation.
3. **Explicit capture** — ``record_event()`` augments the auto
   stream with richly-shaped payloads when needed.
4. **Flush on exit** — the sink writes ``<doc_dir>/telemetry.json``
   when the context manager exits, even if the body raised.
5. **Fail-open** — sink errors NEVER propagate into the pipeline.
6. **Bounded** — runaway loops can't OOM the process or write a
   gigabyte JSON file; ``MAX_EVENTS`` and ``MAX_FIELD_BYTES`` cap
   the output.
7. **Trace-correlated** — each event carries the active trace_id
   + span_id from the existing tracing context.
"""

from __future__ import annotations

import asyncio
import json
import logging as stdlib_logging
import os
from pathlib import Path

import pytest

from app.observability.run_telemetry import (
    MAX_EVENTS,
    RunTelemetrySink,
    current_sink,
    record_event,
    telemetry_run,
)


# ── Isolation ────────────────────────────────────────────────


def test_no_sink_bound_outside_a_run() -> None:
    """The default state must be 'no sink'. Otherwise stray
    log calls in unrelated code would pile up into whichever
    sink last existed."""
    assert current_sink() is None


def test_sink_is_torn_down_after_run(tmp_path: Path) -> None:
    with telemetry_run(doc_id="d1", doc_dir=tmp_path):
        assert current_sink() is not None
    assert current_sink() is None


def test_nested_runs_use_innermost_sink(tmp_path: Path) -> None:
    with telemetry_run(doc_id="outer", doc_dir=tmp_path / "outer") as outer:
        assert current_sink() is outer
        with telemetry_run(doc_id="inner", doc_dir=tmp_path / "inner") as inner:
            assert current_sink() is inner
            assert inner is not outer
        # Inner torn down, outer restored.
        assert current_sink() is outer


@pytest.mark.asyncio
async def test_concurrent_async_runs_do_not_bleed(tmp_path: Path) -> None:
    """ContextVar semantics: two concurrently-running coroutines
    each must see their own sink. The proof: explicitly record
    events from each, then verify neither sink got the other's
    payload.
    """

    barrier_a = asyncio.Event()
    barrier_b = asyncio.Event()
    sinks: dict[str, RunTelemetrySink] = {}

    async def run_a():
        with telemetry_run(doc_id="run-a", doc_dir=tmp_path / "a") as s:
            sinks["a"] = s
            record_event("a-event", value=1)
            barrier_a.set()
            await barrier_b.wait()
            record_event("a-event-2", value=2)

    async def run_b():
        with telemetry_run(doc_id="run-b", doc_dir=tmp_path / "b") as s:
            sinks["b"] = s
            await barrier_a.wait()
            record_event("b-event", value=99)
            barrier_b.set()

    await asyncio.gather(run_a(), run_b())

    a_events = {e.event for e in sinks["a"].events}
    b_events = {e.event for e in sinks["b"].events}
    assert "a-event" in a_events
    assert "a-event-2" in a_events
    assert "b-event" not in a_events
    assert "b-event" in b_events
    assert "a-event" not in b_events


# ── Auto-capture via structlog ───────────────────────────────


def test_logger_calls_are_auto_captured_inside_a_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The headline feature. A plain ``logger.info(...)`` call
    inside ``telemetry_run`` must land in the sink with no
    instrumentation on the call site."""
    from app.observability.logging import configure

    # Configure structlog so the capture processor is in the chain.
    configure(force=True)

    with telemetry_run(doc_id="auto", doc_dir=tmp_path) as sink:
        log = stdlib_logging.getLogger("test.auto_capture")
        log.warning("something_happened", extra={"page": 7})
        log.info("another_event")

    # The capture processor runs on every structlog record, so
    # both stdlib and structlog calls should be in the sink.
    event_names = [e.event for e in sink.events]
    # Match either structured key or the message string itself.
    assert any("something_happened" in str(e.event) or "something_happened" in str(e.fields) for e in sink.events), (
        f"auto-capture missed the warning. Captured: {event_names}"
    )


# ── Explicit record_event ────────────────────────────────────


def test_record_event_attaches_caller_module(tmp_path: Path) -> None:
    with telemetry_run(doc_id="d1", doc_dir=tmp_path) as sink:
        record_event("manual.event", count=42)
    [e] = sink.events_named("manual.event")
    assert e.fields == {"count": 42}
    assert "tests.observability.test_run_telemetry" in e.logger_name


def test_record_event_outside_a_run_is_a_noop() -> None:
    """No sink → no crash, no exception. Calling record_event
    from library code that's exercised in tests should be safe
    even when no run is active."""
    record_event("orphan", x=1)  # must not raise


# ── Flush ────────────────────────────────────────────────────


def test_flush_writes_telemetry_json_with_summary(tmp_path: Path) -> None:
    with telemetry_run(doc_id="d1", doc_dir=tmp_path):
        record_event("a", count=1)
        record_event("a", count=2)
        record_event("b", count=3)

    path = tmp_path / "telemetry.json"
    assert path.exists(), "flush did not write telemetry.json"
    data = json.loads(path.read_text())
    assert data["doc_id"] == "d1"
    assert data["completed_at"] is not None
    assert data["summary"]["events_total"] == 3
    assert data["summary"]["by_event"]["a"] == 2
    assert data["summary"]["by_event"]["b"] == 1


def test_flush_runs_even_on_exception(tmp_path: Path) -> None:
    """The flush must happen on the way out even when the body
    raises — that's where errors are most valuable to capture."""
    with pytest.raises(RuntimeError):
        with telemetry_run(doc_id="boom", doc_dir=tmp_path):
            record_event("seen-before-crash", x=1)
            raise RuntimeError("boom")

    path = tmp_path / "telemetry.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert any(e["event"] == "seen-before-crash" for e in data["events"])


def test_flush_with_no_doc_dir_returns_none() -> None:
    """Test mode: sink can be exercised in-memory without
    touching the filesystem."""
    sink = RunTelemetrySink(doc_id="d1", doc_dir=None)
    sink.record("x", count=1)
    assert sink.flush() is None
    assert len(sink.events) == 1


# ── Fail-open ────────────────────────────────────────────────


def test_unwritable_doc_dir_does_not_raise(monkeypatch, tmp_path: Path) -> None:
    """Flush failure is logged but never raised — telemetry is
    observability, not correctness. Forcing flush to fail must
    not abort the pipeline."""

    # Point doc_dir at a path under a read-only parent. We don't
    # actually need to make it unwritable; passing a "broken"
    # Path-like that fails on mkdir is enough.
    broken_dir = tmp_path / "ro" / "cannot_create"
    # Make ``ro`` read-only so mkdir under it fails.
    (tmp_path / "ro").mkdir()
    os.chmod(tmp_path / "ro", 0o400)
    try:
        with telemetry_run(doc_id="rofail", doc_dir=broken_dir):
            record_event("did-fire", x=1)
        # No assertion on whether the file was written — just
        # that the context manager exited cleanly.
    finally:
        # Restore so pytest can clean up the tree.
        os.chmod(tmp_path / "ro", 0o700)


# ── Bounded ──────────────────────────────────────────────────


def test_overflow_is_recorded_once_then_drops(tmp_path: Path) -> None:
    """A runaway loop hits MAX_EVENTS, a single overflow event
    is recorded, subsequent records are dropped."""
    sink = RunTelemetrySink(doc_id="ovr", doc_dir=None)
    for i in range(MAX_EVENTS + 100):
        sink.record("loop", i=i)

    # The cap is MAX_EVENTS regular events + 1 overflow sentinel.
    assert len(sink.events) <= MAX_EVENTS + 1
    overflow_events = sink.events_named("telemetry.overflow")
    assert len(overflow_events) == 1, "overflow must be recorded exactly once"


def test_long_string_fields_are_truncated(tmp_path: Path) -> None:
    from app.observability.run_telemetry import MAX_FIELD_BYTES

    sink = RunTelemetrySink(doc_id="trunc", doc_dir=None)
    long_string = "a" * (MAX_FIELD_BYTES * 2)
    sink.record("big", payload=long_string)
    [e] = sink.events
    assert len(e.fields["payload"].encode("utf-8")) < MAX_FIELD_BYTES * 2
    assert e.fields["payload"].endswith("[truncated]")


# ── Trace correlation ────────────────────────────────────────


def test_events_carry_trace_id_when_a_trace_is_active(tmp_path: Path) -> None:
    """Every event must carry the active trace_id + span_id so
    log records and on-disk telemetry can be cross-correlated."""
    from app.observability.tracing import span

    with telemetry_run(doc_id="trace", doc_dir=tmp_path) as sink:
        with span("test.span") as ctx:
            record_event("inside-span", x=1)
            expected_trace = ctx.trace_id
            expected_span = ctx.span_id

    [e] = sink.events_named("inside-span")
    assert e.trace_id == expected_trace
    assert e.span_id == expected_span


# ── Summary semantics ────────────────────────────────────────


def test_summary_aggregates_by_event_logger_and_level() -> None:
    sink = RunTelemetrySink(doc_id="s", doc_dir=None)
    sink.record("evt-a", level="info", logger_name="mod.x", k=1)
    sink.record("evt-a", level="info", logger_name="mod.x", k=2)
    sink.record("evt-b", level="warning", logger_name="mod.y", k=3)

    summary = sink.summary()
    assert summary["events_total"] == 3
    assert summary["by_event"] == {"evt-a": 2, "evt-b": 1}
    assert summary["by_logger"] == {"mod.x": 2, "mod.y": 1}
    assert summary["by_level"] == {"info": 2, "warning": 1}
