"""FR-001, FR-007: middleware parses/emits traceparent and fails open."""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability import configure as configure_obs
from app.observability.middleware import install as install_obs


@pytest.fixture
def client() -> Iterator[TestClient]:
    configure_obs(force=True)
    app = FastAPI()
    install_obs(app)

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"pong": "pong"}

    @app.get("/boom")
    def boom() -> dict[str, str]:
        raise RuntimeError("deliberate")

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


_TP_RE = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


def test_response_has_minted_trace_headers(client: TestClient) -> None:
    r = client.get("/ping")
    assert r.status_code == 200
    assert "traceparent" in r.headers
    assert _TP_RE.match(r.headers["traceparent"]), r.headers["traceparent"]
    assert "x-request-id" in r.headers
    assert len(r.headers["x-request-id"]) == 32


def test_inbound_traceparent_is_honoured(client: TestClient) -> None:
    tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    r = client.get("/ping", headers={"traceparent": tp})
    # The trace_id survives; the span_id is the handler's child (new).
    assert r.headers["x-request-id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    out = r.headers["traceparent"]
    m = _TP_RE.match(out)
    assert m is not None
    assert out.split("-")[1] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert out.split("-")[2] != "00f067aa0ba902b7"  # new child span


def test_malformed_traceparent_is_tolerated(client: TestClient) -> None:
    r = client.get("/ping", headers={"traceparent": "not-a-trace"})
    assert r.status_code == 200  # never reject
    assert "traceparent" in r.headers


def test_exception_counts_and_preserves_trace(client: TestClient) -> None:
    from app.observability.metrics import ERRORS

    before = 0
    # prometheus client doesn't expose value directly; read through samples.
    for sample in ERRORS.collect()[0].samples:
        before += int(sample.value) if sample.name.endswith("_total") else 0

    r = client.get("/boom")
    # With raise_server_exceptions=False, starlette returns 500.
    assert r.status_code == 500
    assert "traceparent" in r.headers  # still emitted on failure

    after = 0
    for sample in ERRORS.collect()[0].samples:
        after += int(sample.value) if sample.name.endswith("_total") else 0
    assert after >= before + 1


def test_metrics_endpoint_returns_prometheus_format() -> None:
    configure_obs(force=True)
    app = FastAPI()
    install_obs(app)
    with TestClient(app) as c:
        r = c.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    # At least the built-in metric names are present.
    assert "http_requests_total" in r.text
