"""``/health``, ``/health/ready``, ``/metrics`` — served outside the auth gate.

Liveness vs readiness split (k8s convention):

* ``/health``      — the process is alive (cheap, always returns 200 if we
  can serve at all).
* ``/health/ready`` — the process can accept traffic: storage dir writable,
  rule bank importable, event bus initialised.
* ``/metrics``     — Prometheus text exposition. Safe to scrape.

No dependency on :mod:`app.api.deps` (`require_actor`) — these are
platform-internal surfaces.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.observability.logging import get_logger
from app.observability.metrics import HEALTHCHECKS, REGISTRY

logger = get_logger(__name__)

router = APIRouter()


@router.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    HEALTHCHECKS.labels(endpoint="health", status="ok").inc()
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def ready(response: Response) -> dict[str, object]:
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    # Storage writable?
    try:
        from app.config.settings import get_settings

        base = Path(get_settings().storage.base_path)
        base.mkdir(parents=True, exist_ok=True)
        probe = base / ".ready"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks["storage"] = True
    except Exception as exc:  # pragma: no cover — env-specific
        checks["storage"] = False
        reasons.append(f"storage: {exc}")

    # Event bus initialised?
    try:
        from app.bmr.events import get_event_bus

        bus = get_event_bus()
        checks["event_bus"] = bus is not None
    except Exception as exc:
        checks["event_bus"] = False
        reasons.append(f"event_bus: {exc}")

    # Pilot rule bank importable?
    try:
        from app.bmr.rules.loader import load_rule_bank

        pilot = (
            Path(__file__).resolve().parents[2]
            / "config"
            / "rules"
            / "pilot"
            / "bank"
        )
        bank = load_rule_bank(pilot)
        checks["rule_bank"] = bank.ok
        if not bank.ok:
            reasons.append("rule_bank: bank reports errors")
    except Exception as exc:
        checks["rule_bank"] = False
        reasons.append(f"rule_bank: {exc}")

    all_ok = all(checks.values())
    HEALTHCHECKS.labels(
        endpoint="ready", status="ok" if all_ok else "failed"
    ).inc()
    if not all_ok:
        response.status_code = 503
    return {"status": "ok" if all_ok else "degraded", "checks": checks, "reasons": reasons}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    payload = generate_latest(REGISTRY)
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)


__all__ = ["router"]
