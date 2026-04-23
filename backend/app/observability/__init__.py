"""Cross-cutting observability: tracing, structured logging, metrics.

Public surface kept narrow. Domain code imports from here; nothing here
imports from ``app.bmr`` or ``app.compliance`` — SoC is preserved.
"""

from app.observability.context import (
    RequestScope,
    TraceContext,
    bind_context,
    current_scope,
    current_trace,
    reset_context,
)
from app.observability.logging import configure, get_logger
from app.observability.tracing import (
    mint_trace,
    parse_traceparent,
    span,
    submit_with_context,
    traced,
)

__all__ = [
    "RequestScope",
    "TraceContext",
    "bind_context",
    "configure",
    "current_scope",
    "current_trace",
    "get_logger",
    "mint_trace",
    "parse_traceparent",
    "reset_context",
    "span",
    "submit_with_context",
    "traced",
]
