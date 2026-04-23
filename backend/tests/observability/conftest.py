"""Reset observability state between tests so counters + contextvars don't leak."""

from __future__ import annotations

import pytest

from app.observability import metrics as metrics_mod
from app.observability.context import REQUEST_SCOPE, TRACE_CTX, RequestScope


@pytest.fixture(autouse=True)
def _reset_observability() -> None:
    metrics_mod.reset_for_tests()
    # Reset the ContextVars by re-seeding defaults.
    TRACE_CTX.set(None)
    REQUEST_SCOPE.set(RequestScope())
    yield
    metrics_mod.reset_for_tests()
