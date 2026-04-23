"""NFR-001 / SC-006: observability middleware adds ≤ 5 ms to p95.

Marked slow — opt-in via ``pytest -m slow``. Asserts a generous upper
bound (5 ms) on the delta between observability-on and
observability-off for an otherwise-trivial handler.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability.middleware import install as install_obs

_N = 200  # Enough samples for a stable p95 on a trivial path.


def _measure(build_app: Callable[[], FastAPI]) -> list[float]:
    app = build_app()

    @app.get("/noop")
    def noop() -> dict[str, str]:
        return {"ok": "ok"}

    times: list[float] = []
    with TestClient(app) as c:
        # Warm up
        for _ in range(10):
            c.get("/noop")
        for _ in range(_N):
            t0 = time.perf_counter()
            c.get("/noop")
            times.append((time.perf_counter() - t0) * 1_000)  # ms
    return times


@pytest.mark.slow
def test_observability_overhead_p95_under_5ms() -> None:
    baseline = _measure(lambda: FastAPI())

    def _with_obs() -> FastAPI:
        app = FastAPI()
        install_obs(app)
        return app

    instrumented = _measure(_with_obs)

    p95_base = statistics.quantiles(baseline, n=20)[-1]  # 95th percentile
    p95_instr = statistics.quantiles(instrumented, n=20)[-1]
    delta = p95_instr - p95_base
    # Generous bound — test runners vary; we just want to catch pathological
    # regressions (orders of magnitude, not microseconds).
    assert delta < 5.0, (
        f"observability overhead p95 = {delta:.2f}ms (> 5 ms budget)."
        f" baseline p95={p95_base:.2f}ms, instrumented p95={p95_instr:.2f}ms"
    )
