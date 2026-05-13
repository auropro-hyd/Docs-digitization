"""Pin the logging-hygiene contracts (quiet routes, single-render,
compact dev renderer).

Background: the 2026-05-13 Datalab wait dumped ~200 polling-driven
trace lines per 200 s window into the developer log, each with a
double ``[info     ] [info     ]`` prefix, repeated
``parent_span_id=... span_id=... trace_id=... ts=...`` suffixes, AND
a parallel uvicorn access line. This module pins the fix:

  * Polling routes (``/progress``, doc/run GET, /health, /metrics)
    log ``trace.request.started/finished`` at DEBUG, not INFO —
    invisible at INFO log level.
  * Real state-changing routes still log at INFO.
  * Structlog ↔ stdlib bridge uses ``ProcessorFormatter.wrap_for
    _formatter`` so the renderer runs exactly once (was running
    twice, producing the double prefix and duplicated trace ID
    blocks).
  * Dev terminal renderer drops ``trace_id`` / ``span_id`` /
    ``parent_span_id`` from each line (the values still survive in
    the JSON sink / on-disk telemetry).
  * uvicorn access log is demoted to WARNING so successful polls
    don't add a third line per request.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest


class _Capture(logging.Handler):
    """Capture every emitted record via the formatter attached to the
    production root handler — mirrors the way real stdout sees them.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[str] = []
        self.raw: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.raw.append(record)
        try:
            self.records.append(self.format(record))
        except Exception:
            self.records.append(record.getMessage())


# ── Quiet-routes demotion ──────────────────────────────────────


def test_quiet_route_classification() -> None:
    """The polling endpoints the frontend hits every 1-2 s during
    a long pipeline run must be classified as quiet so their trace
    events demote to DEBUG. Pin both the include list and the
    state-changing-routes-stay-INFO control case."""

    from app.observability.middleware import _is_quiet_route

    assert _is_quiet_route("GET", "/api/documents/{doc_id}/progress")
    assert _is_quiet_route("GET", "/api/documents/{doc_id}")
    assert _is_quiet_route("GET", "/api/runs/{run_id}/progress")
    assert _is_quiet_route("GET", "/health")
    assert _is_quiet_route("GET", "/metrics")

    # State-changing operations stay loud — these are NOT polling.
    assert not _is_quiet_route("POST", "/api/documents/{doc_id}/run")
    assert not _is_quiet_route("PATCH", "/api/documents/{doc_id}")
    assert not _is_quiet_route("DELETE", "/api/documents/{doc_id}")
    # POST on the same path as a quiet GET stays loud.
    assert not _is_quiet_route("POST", "/api/documents/{doc_id}/progress")


def test_quiet_route_trace_events_demoted_to_debug(monkeypatch) -> None:
    """End-to-end: hitting a quiet route at log level INFO must
    produce NO ``trace.request.started/finished`` records. The
    middleware records metrics + still bumps the contextvar, but
    the line never reaches stdout."""

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.observability import configure, get_logger
    from app.observability.middleware import install as install_obs

    monkeypatch.setenv("AT_OBS__LOG_MODE", "json")
    monkeypatch.setenv("AT_OBS__LOG_LEVEL", "INFO")
    configure(force=True)

    app = FastAPI()

    @app.get("/api/documents/{doc_id}/progress")
    def progress(doc_id: str) -> dict[str, str]:
        return {"status": "running"}

    install_obs(app)

    capture = _Capture()
    root = logging.getLogger()
    if root.handlers:
        capture.setFormatter(root.handlers[0].formatter)
    root.addHandler(capture)
    try:
        with TestClient(app) as client:
            client.get("/api/documents/doc-1/progress")
    finally:
        root.removeHandler(capture)

    rendered_events = []
    for line in capture.records:
        try:
            rendered_events.append(json.loads(line).get("event"))
        except Exception:
            pass

    assert "trace.request.started" not in rendered_events, (
        f"quiet-route progress GET must not emit trace.request.started "
        f"at INFO; got events: {rendered_events}"
    )
    assert "trace.request.finished" not in rendered_events


def test_non_quiet_route_still_logs_trace_at_info(monkeypatch) -> None:
    """Control case — a state-changing endpoint must still produce
    the trace events at INFO. The demotion is scoped to the
    polling allowlist, not a global quieting."""

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.observability import configure
    from app.observability.middleware import install as install_obs

    monkeypatch.setenv("AT_OBS__LOG_MODE", "json")
    monkeypatch.setenv("AT_OBS__LOG_LEVEL", "INFO")
    configure(force=True)

    app = FastAPI()

    @app.post("/api/documents/{doc_id}/run")
    def run(doc_id: str) -> dict[str, str]:
        return {"started": "ok"}

    install_obs(app)

    capture = _Capture()
    root = logging.getLogger()
    if root.handlers:
        capture.setFormatter(root.handlers[0].formatter)
    root.addHandler(capture)
    try:
        with TestClient(app) as client:
            client.post("/api/documents/doc-1/run")
    finally:
        root.removeHandler(capture)

    rendered_events = []
    for line in capture.records:
        try:
            rendered_events.append(json.loads(line).get("event"))
        except Exception:
            pass

    assert "trace.request.started" in rendered_events
    assert "trace.request.finished" in rendered_events


# ── Single-render contract (no doubled level prefix) ──────────


def test_dev_renderer_does_not_double_render_level_prefix(monkeypatch) -> None:
    """The structlog ↔ stdlib bridge used to apply the renderer
    twice, producing ``[info     ] [info     ] event``. With the
    ``wrap_for_formatter`` pattern the renderer runs exactly once.
    Pin by asserting the ``[info     ]`` token appears at most once
    per rendered line."""

    from app.observability import configure, get_logger

    monkeypatch.setenv("AT_OBS__LOG_MODE", "dev")
    monkeypatch.setenv("AT_OBS__LOG_LEVEL", "INFO")
    configure(force=True)

    log = get_logger("test.double_render")
    capture = _Capture()
    root = logging.getLogger()
    if root.handlers:
        capture.setFormatter(root.handlers[0].formatter)
    root.addHandler(capture)
    try:
        log.info("test.event_doubling")
    finally:
        root.removeHandler(capture)

    # The dev renderer color codes the level. Strip ANSI escapes
    # before counting; otherwise the colourized token shape is
    # `[\x1b[32minfo     \x1b[0m]` not `[info     ]`.
    import re as _re
    ansi = _re.compile(r"\x1b\[[0-9;]*m")
    for line in capture.records:
        stripped = ansi.sub("", line)
        info_count = stripped.count("[info")
        assert info_count <= 1, (
            f"level prefix rendered more than once on a single line — "
            f"the wrap_for_formatter bridge is regressed. Line: {line!r}"
        )


# ── Compact dev renderer (no verbose trace-id triple) ─────────


def test_dev_renderer_strips_trace_id_keys(monkeypatch) -> None:
    """The dev renderer must drop ``trace_id`` / ``span_id`` /
    ``parent_span_id`` from terminal output. The values are still
    captured by the telemetry sink and the JSON renderer keeps
    them — only the dev/terminal stream is compacted."""

    from app.observability import configure, get_logger
    from app.observability.context import bind_context

    monkeypatch.setenv("AT_OBS__LOG_MODE", "dev")
    monkeypatch.setenv("AT_OBS__LOG_LEVEL", "INFO")
    configure(force=True)

    log = get_logger("test.compact_dev")
    capture = _Capture()
    root = logging.getLogger()
    if root.handlers:
        capture.setFormatter(root.handlers[0].formatter)
    root.addHandler(capture)
    try:
        # Emulate the trace-context injection that the middleware
        # would do — without it the keys are never set, so the test
        # would tautologically pass.
        from app.observability.context import set_trace
        from app.observability.tracing import mint_trace
        tok = set_trace(mint_trace())
        try:
            log.info("test.compact")
        finally:
            from app.observability.context import reset_trace
            reset_trace(tok)
    finally:
        root.removeHandler(capture)

    blob = "\n".join(capture.records)
    assert "trace_id=" not in blob, (
        f"dev renderer must strip trace_id from terminal output; got:\n{blob}"
    )
    assert "span_id=" not in blob
    assert "parent_span_id=" not in blob


def test_json_renderer_keeps_trace_id_keys(monkeypatch) -> None:
    """Control case: the JSON renderer (production / on-disk) must
    NOT strip the trace ID keys — they're the only thing tying log
    lines to a request trail in distributed-tracing dashboards."""

    from app.observability import configure, get_logger
    from app.observability.context import set_trace, reset_trace
    from app.observability.tracing import mint_trace

    monkeypatch.setenv("AT_OBS__LOG_MODE", "json")
    monkeypatch.setenv("AT_OBS__LOG_LEVEL", "INFO")
    configure(force=True)

    log = get_logger("test.json_trace")
    capture = _Capture()
    root = logging.getLogger()
    if root.handlers:
        capture.setFormatter(root.handlers[0].formatter)
    root.addHandler(capture)
    try:
        tok = set_trace(mint_trace())
        try:
            log.info("test.json_keeps_trace")
        finally:
            reset_trace(tok)
    finally:
        root.removeHandler(capture)

    found_with_trace = False
    for line in capture.records:
        try:
            j = json.loads(line)
        except Exception:
            continue
        if j.get("event") == "test.json_keeps_trace":
            assert "trace_id" in j and "span_id" in j, (
                f"JSON renderer must keep trace IDs; got {j}"
            )
            found_with_trace = True
            break
    assert found_with_trace, "test event was not captured in JSON output"


# ── uvicorn access log quieted ─────────────────────────────────


def test_uvicorn_access_log_demoted_to_warning(monkeypatch) -> None:
    """The uvicorn.access logger duplicates the trace middleware's
    events AND ignores our quiet-routes demotion (logs everything
    at INFO regardless). Demoted to WARNING in ``configure()`` so
    only abnormal responses surface."""

    from app.observability import configure

    monkeypatch.setenv("AT_OBS__LOG_MODE", "json")
    monkeypatch.setenv("AT_OBS__LOG_LEVEL", "INFO")
    configure(force=True)

    uv = logging.getLogger("uvicorn.access")
    assert uv.level == logging.WARNING, (
        f"uvicorn.access should be at WARNING after configure(); "
        f"got level {uv.level} ({logging.getLevelName(uv.level)})"
    )
