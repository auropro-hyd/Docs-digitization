"""End-to-end: a BMR HTTP request's trace id reaches stage-level logs.

Not a strict smoke — the app has lots of moving parts. We just exercise the
lightest BMR path we can find (list runs) and assert the response carries
the `traceparent` echoing what we sent in. Full log-capture correlation
lives in ``test_trace_propagation.py`` which uses an in-process app.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

_KNOWN_TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def test_bmr_list_runs_echoes_trace() -> None:
    with TestClient(create_app()) as c:
        r = c.get(
            "/api/bmr/runs",
            headers={
                "traceparent": _KNOWN_TP,
                "X-Actor-Id": "test.actor",
            },
        )
    # require_actor auth gate is in place — 200 expected because X-Actor-Id is valid.
    assert r.status_code == 200, r.text
    out = r.headers.get("traceparent")
    assert out is not None
    # Same trace id, new child span.
    assert out.split("-")[1] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert out.split("-")[2] != "00f067aa0ba902b7"
    assert r.headers.get("x-request-id") == "4bf92f3577b34da6a3ce929d0e0e4736"
