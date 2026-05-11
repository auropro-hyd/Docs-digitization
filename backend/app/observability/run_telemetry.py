"""Run-scoped telemetry capture.

WHY THIS EXISTS

The existing observability stack — structlog JSON logging + Prometheus
metrics + trace context propagation — captures events transiently:
they go to stdout and a metrics endpoint, but no per-run record is
written to disk. Post-run validation of "did step X actually fire?"
requires either live log capture or a re-run with stdout teed to a
file. That's fragile.

This module adds a **per-run on-disk telemetry record** without
forcing every step in the pipeline to know about it. Two ingestion
paths feed one sink:

1. **Implicit (zero-instrumentation)** — a structlog processor
   (added in ``logging.py``) mirrors every log record into the
   active sink. Any existing ``logger.info(...)`` / ``logger.warning(...)``
   call in pipeline code becomes durable telemetry, automatically,
   no code change needed.

2. **Explicit (when richer than a log line)** — call
   :func:`record_event` from anywhere to attach a structured
   payload to the sink. Used for things like the signature
   enricher's per-layer counts, where the natural shape is a
   dict rather than a string log message.

DESIGN INVARIANTS

* **ContextVar-scoped.** A sink is bound at run start via
  :func:`telemetry_run` and torn down at run end. Concurrent runs
  each carry their own sink — no cross-contamination.
* **Fail-open.** Sink failures (disk full, malformed payload,
  serialization issues) never raise into the pipeline. Telemetry
  is an observability surface, not a correctness surface.
* **Bounded.** ``MAX_EVENTS`` and ``MAX_FIELD_BYTES`` cap the sink's
  growth so a runaway loop doesn't OOM the process or write a
  gigabyte JSON file.
* **Trace-correlated.** Every event carries the active
  ``trace_id`` + ``span_id`` from the existing tracing context,
  so logs and on-disk telemetry use the same correlation keys.
* **Self-summarizing.** A ``summary`` block is computed at flush
  time grouping events by (logger, event, level) — quick to
  read without scanning the full event stream.

USAGE

::

    from app.observability.run_telemetry import telemetry_run, record_event

    async def run_compliance_pipeline(doc_id, doc_dir, ...):
        with telemetry_run(doc_id=doc_id, doc_dir=doc_dir):
            # Everything inside flows into doc_dir/telemetry.json
            # at exit — including all logger.*() calls and any
            # explicit record_event() calls.
            ...
            record_event(
                "signature.enriched",
                page_num=3,
                layer_counts={"L4": 22, ...},
            )
            ...

Test surfaces are pure: the sink can be exercised without a
filesystem (pass ``doc_dir=None``) and asserted against directly.
"""

from __future__ import annotations

import json
import logging as stdlib_logging
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from app.observability.tracing import current_trace

logger = stdlib_logging.getLogger(__name__)


# Tunable caps — keep telemetry from growing unboundedly. Both can
# be overridden via env (deliberately not exposed in DatalabConfig /
# similar; this is an observability tunable, not a product config).
MAX_EVENTS = 50_000
"""Hard cap on events captured per run. Beyond this, additional
events are dropped (a single ``telemetry.overflow`` event is
recorded once)."""

MAX_FIELD_BYTES = 16 * 1024
"""Per-field truncation. A log record with a 5MB ``markdown`` field
shouldn't get persisted in full — the on-disk telemetry would
balloon. Long strings are truncated with a ``[truncated]`` suffix."""


@dataclass
class TelemetryEvent:
    """One structured event captured by the sink."""

    ts: str
    level: str
    logger_name: str
    event: str
    trace_id: str = ""
    span_id: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "level": self.level,
            "logger": self.logger_name,
            "event": self.event,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "fields": self.fields,
        }


@dataclass
class RunTelemetrySink:
    """Captures structured events for the duration of a single run.

    Pass ``doc_dir=None`` to use the sink in tests without touching
    the filesystem.
    """

    doc_id: str
    doc_dir: Path | None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    events: list[TelemetryEvent] = field(default_factory=list)
    _overflow_recorded: bool = field(default=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ── Recording ────────────────────────────────────────────

    def record(
        self,
        event: str,
        *,
        level: str = "info",
        logger_name: str = "",
        **fields: Any,
    ) -> None:
        """Capture one event. Safe to call from any thread.

        Field values are truncated per :data:`MAX_FIELD_BYTES`; the
        full event list is capped at :data:`MAX_EVENTS` with an
        overflow sentinel emitted once on first overflow.
        """

        try:
            with self._lock:
                if len(self.events) >= MAX_EVENTS:
                    if not self._overflow_recorded:
                        self._overflow_recorded = True
                        self.events.append(
                            TelemetryEvent(
                                ts=datetime.now(UTC).isoformat(),
                                level="warning",
                                logger_name="app.observability.run_telemetry",
                                event="telemetry.overflow",
                                fields={"max_events": MAX_EVENTS},
                            )
                        )
                    return

                tc = current_trace()
                trace_id = tc.trace_id if tc else ""
                span_id = tc.span_id if tc else ""

                self.events.append(
                    TelemetryEvent(
                        ts=datetime.now(UTC).isoformat(),
                        level=level,
                        logger_name=logger_name,
                        event=event,
                        trace_id=trace_id,
                        span_id=span_id,
                        fields=_truncate_fields(fields),
                    )
                )
        except Exception:  # pragma: no cover — defensive
            # Never raise from telemetry capture.
            logger.exception("RunTelemetrySink.record failed for %s", event)

    # ── Inspection ───────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        """Auto-aggregate the event stream.

        Returns counts by (event, logger, level). Cheap; intended
        for direct read during validation or post-mortem.
        """
        by_event: dict[str, int] = {}
        by_logger: dict[str, int] = {}
        by_level: dict[str, int] = {}
        for e in self.events:
            by_event[e.event] = by_event.get(e.event, 0) + 1
            by_logger[e.logger_name] = by_logger.get(e.logger_name, 0) + 1
            by_level[e.level] = by_level.get(e.level, 0) + 1
        return {
            "events_total": len(self.events),
            "by_event": dict(sorted(by_event.items(), key=lambda x: -x[1])),
            "by_logger": dict(sorted(by_logger.items(), key=lambda x: -x[1])),
            "by_level": dict(sorted(by_level.items(), key=lambda x: -x[1])),
        }

    def events_named(self, *names: str) -> list[TelemetryEvent]:
        """Filter events by name. Used by tests + post-run validation."""
        wanted = set(names)
        return [e for e in self.events if e.event in wanted]

    # ── Flush ────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "duration_seconds": (
                (self.completed_at - self.started_at).total_seconds()
                if self.completed_at
                else None
            ),
            "summary": self.summary(),
            "events": [e.to_dict() for e in self.events],
        }

    def flush(self) -> Path | None:
        """Write the sink to ``<doc_dir>/telemetry.json`` and return
        the path. Returns None when no ``doc_dir`` is bound (test mode).

        Fail-open: serialization errors are logged but do not raise.
        """
        self.completed_at = datetime.now(UTC)
        if self.doc_dir is None:
            return None
        try:
            self.doc_dir.mkdir(parents=True, exist_ok=True)
            out = self.doc_dir / "telemetry.json"
            out.write_text(
                json.dumps(self.to_dict(), indent=2, default=str),
                encoding="utf-8",
            )
            return out
        except Exception:  # pragma: no cover — defensive
            logger.exception("telemetry flush failed for %s", self.doc_id)
            return None


# ── ContextVar plumbing ──────────────────────────────────────


_RUN_TELEMETRY: ContextVar[RunTelemetrySink | None] = ContextVar(
    "run_telemetry", default=None
)


def current_sink() -> RunTelemetrySink | None:
    """The sink bound to the active run, or None if no run is active."""
    return _RUN_TELEMETRY.get()


@contextmanager
def telemetry_run(
    doc_id: str,
    doc_dir: Path | None,
) -> Iterator[RunTelemetrySink]:
    """Bind a fresh sink for the duration of the ``with`` block.

    Inside the block:
    * All ``logger.*()`` calls (via the structlog capture processor
      in ``logging.py``) are mirrored into the sink.
    * Explicit :func:`record_event` calls land in the sink.
    * The sink is flushed to ``<doc_dir>/telemetry.json`` on exit.

    Concurrent ``telemetry_run`` blocks each carry their own sink —
    ``current_sink()`` returns the innermost active one.
    """
    sink = RunTelemetrySink(doc_id=doc_id, doc_dir=doc_dir)
    token = _RUN_TELEMETRY.set(sink)
    try:
        yield sink
    finally:
        try:
            sink.flush()
        except Exception:  # pragma: no cover — defensive
            logger.exception("telemetry_run cleanup failed for %s", doc_id)
        _RUN_TELEMETRY.reset(token)


def record_event(event: str, *, level: str = "info", **fields: Any) -> None:
    """Record one telemetry event. No-op if no sink is bound.

    Use this when the payload shape is structurally richer than a
    log message — e.g. per-page enrichment counters, batch
    completion summaries.

    Existing ``logger.*()`` calls don't need this; they're captured
    automatically by the structlog processor.
    """
    sink = current_sink()
    if sink is None:
        return
    # Caller's module name as logger name when possible.
    import inspect

    frame = inspect.currentframe()
    caller_module = ""
    if frame and frame.f_back:
        caller_module = frame.f_back.f_globals.get("__name__", "")
    sink.record(event=event, level=level, logger_name=caller_module, **fields)


# ── Helpers ──────────────────────────────────────────────────


def _truncate_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Truncate any string field exceeding ``MAX_FIELD_BYTES``.

    Non-string fields pass through unchanged. Nested dicts/lists are
    not deeply walked — the cap targets the common case (a long
    markdown / HTML payload accidentally bound to a log record).
    """
    out: dict[str, Any] = {}
    for k, v in fields.items():
        if isinstance(v, str) and len(v.encode("utf-8")) > MAX_FIELD_BYTES:
            out[k] = v[:MAX_FIELD_BYTES] + "...[truncated]"
        else:
            out[k] = v
    return out


__all__ = [
    "MAX_EVENTS",
    "MAX_FIELD_BYTES",
    "RunTelemetrySink",
    "TelemetryEvent",
    "current_sink",
    "record_event",
    "telemetry_run",
]
