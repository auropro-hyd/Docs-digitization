"""SC-001 end-to-end: one ``traceparent`` correlates every log line.

Stands up a minimal FastAPI app, submits one request with a known
``traceparent``, captures structlog output, and asserts every record emitted
during that request carries the same ``trace_id``. Also exercises a worker-
thread submission to prove FR-002's cross-thread propagation end-to-end.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability import configure, get_logger
from app.observability.middleware import install as install_obs
from app.observability.tracing import submit_with_context

_TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


class _JsonCapture(logging.Handler):
    """Capture every emitted record's message as a JSON dict (best-effort)."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        try:
            parsed = json.loads(msg)
        except Exception:
            parsed = {"raw": msg}
        self.records.append(parsed)


def test_single_traceparent_correlates_every_log_record(monkeypatch) -> None:
    monkeypatch.setenv("AT_OBS__LOG_MODE", "json")
    configure(force=True)
    log = get_logger("test.trace")

    app = FastAPI()
    install_obs(app)

    @app.get("/ping")
    def ping() -> dict[str, str]:
        log.info("test.work.doing")
        # Kick off a worker thread — its log must carry the same trace id.
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = submit_with_context(ex, _worker)
            fut.result(timeout=2)
        return {"pong": "pong"}

    capture = _JsonCapture()
    root = logging.getLogger()
    # After the logging-hygiene fix, structlog uses
    # ``ProcessorFormatter.wrap_for_formatter`` so structlog records
    # arrive at handlers as a wrapped event_dict, not a pre-rendered
    # string. The capture handler has to apply the same formatter
    # as the production handler to recover the rendered JSON output.
    if root.handlers:
        capture.setFormatter(root.handlers[0].formatter)
    root.addHandler(capture)
    try:
        with TestClient(app) as c:
            c.get("/ping", headers={"traceparent": _TP})
    finally:
        root.removeHandler(capture)

    # All records that carry a trace_id, for this request, must share _TRACE_ID.
    traced = [r for r in capture.records if "trace_id" in r]
    assert traced, "no records were captured with a trace_id"
    bad = [r for r in traced if r["trace_id"] != _TRACE_ID]
    assert not bad, f"some records drifted off the inbound trace id: {bad[:3]}"
    # At minimum we should see trace.request.started, test.work.doing,
    # test.worker.doing, trace.request.finished — four events.
    seen = {r.get("event") for r in traced}
    assert {
        "trace.request.started",
        "test.work.doing",
        "test.worker.doing",
        "trace.request.finished",
    } <= seen, f"missing expected events; saw {seen}"


def _worker() -> None:
    get_logger("test.worker").info("test.worker.doing")
