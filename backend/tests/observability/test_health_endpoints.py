"""FR-009 / FR-016: /health, /health/ready, /metrics served without auth."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_and_metrics_reachable_without_actor_header() -> None:
    # NOTE: require_actor guards BMR routes. These three endpoints explicitly
    # must NOT require it.
    with TestClient(create_app()) as c:
        for path in ("/health", "/metrics"):
            r = c.get(path)
            assert r.status_code == 200, (path, r.text[:200])


def test_ready_returns_json_with_checks() -> None:
    with TestClient(create_app()) as c:
        r = c.get("/health/ready")
    # 200 (all green) or 503 (something failed); both are well-formed.
    assert r.status_code in (200, 503)
    body = r.json()
    assert set(body.keys()) >= {"status", "checks", "reasons"}
    assert isinstance(body["checks"], dict)
    # Known probe keys.
    for expected in ("storage", "event_bus", "rule_bank"):
        assert expected in body["checks"]
