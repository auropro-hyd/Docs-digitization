"""FastAPI middleware — bind trace + scope, emit request metrics + events.

The middleware is intentionally the *only* FastAPI-aware file in this
package. Domain code accepts `Logger` / metric helpers via imports, not via
`Request` objects.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response
from starlette.routing import Match

from app.observability.context import (
    TRACE_CTX,
    bind_context,
    reset_context,
    reset_trace,
    set_trace,
)
from app.observability.logging import get_logger
from app.observability.metrics import ERRORS, HTTP_DURATION, HTTP_REQUESTS
from app.observability.tracing import (
    mint_trace,
    parse_traceparent,
    try_from_request_id,
)

logger = get_logger(__name__)


def _status_class(status_code: int) -> str:
    return f"{status_code // 100}xx"


def _route_template(request: Request) -> str:
    """Return the FastAPI path template (``/api/runs/{run_id}``) instead of
    the concrete path, so metrics don't blow up on per-id cardinality.
    """

    app = request.scope.get("app")
    if app is None:
        return request.url.path
    for route in app.routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            return getattr(route, "path", request.url.path)
    return request.url.path


def _path_params(request: Request) -> dict[str, Any]:
    return request.scope.get("path_params") or {}


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Parse inbound traceparent, bind request scope, emit telemetry.

    Fail-open: any exception from the observability layer itself is logged
    and the request is allowed through. The goal is that a bug in our logs
    never 500s a customer request.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.monotonic()

        # 1. Resolve the incoming trace.
        header = request.headers.get("traceparent")
        ctx = parse_traceparent(header)
        source = "inbound"
        if ctx is None:
            if header:
                logger.warning(
                    "trace.malformed_header",
                    raw_header=header[:128],
                )
            hint = try_from_request_id(request.headers.get("X-Request-Id"))
            ctx = hint or mint_trace()
            source = "minted" if hint is None else "inbound"

        # The parsed ctx has span_id = caller's span. The handler runs under
        # a *new child* span so downstream clients can chain their own
        # children from the emitted traceparent.
        handler_ctx = ctx.child_span(secrets.token_hex(8))
        trace_token = set_trace(handler_ctx)

        # 2. Bind business ids from path params + known headers.
        path_params = _path_params(request)
        scope_fields: dict[str, Any] = {}
        for k in ("doc_id", "run_id"):
            v = path_params.get(k)
            if isinstance(v, str):
                scope_fields[k] = v
        actor = request.headers.get("X-Actor-Id")
        if isinstance(actor, str) and actor:
            scope_fields["actor_id"] = actor
        scope_token = bind_context(**scope_fields) if scope_fields else None

        route = _route_template(request)
        method = request.method

        logger.info(
            "trace.request.started",
            source=source,
            route=route,
            method=method,
        )

        response: Response | None = None
        try:
            response = await call_next(request)
        except Exception as exc:
            # Unhandled. Log + count, then synthesise a 500 response so we
            # can still emit the correlation headers — clients chasing a
            # failure still need the trace id. We intentionally do NOT
            # re-raise: letting Starlette's ServerErrorMiddleware turn the
            # exception into a bare 500 would drop our headers.
            logger.exception(
                "error.unhandled",
                route=route,
                error_kind=type(exc).__name__,
                error_msg=str(exc),
            )
            import contextlib

            with contextlib.suppress(Exception):  # pragma: no cover — fail-open
                ERRORS.labels(route=route, kind=type(exc).__name__).inc()
            response = JSONResponse(
                status_code=500,
                content={
                    "detail": "internal server error",
                    "error_kind": type(exc).__name__,
                },
            )
        finally:
            duration = time.monotonic() - started
            status_code = response.status_code if response is not None else 500
            try:
                HTTP_REQUESTS.labels(
                    method=method,
                    route=route,
                    status_class=_status_class(status_code),
                ).inc()
                HTTP_DURATION.labels(method=method, route=route).observe(duration)
            except Exception:
                pass

            # Emit tracing headers on the way out.
            if response is not None:
                try:
                    handler_span = TRACE_CTX.get()
                    if handler_span is not None:
                        response.headers["traceparent"] = handler_span.to_header()
                        response.headers["X-Request-Id"] = handler_span.trace_id
                    if handler_span is not None and handler_span.tracestate:
                        response.headers["tracestate"] = handler_span.tracestate
                    existing_expose = response.headers.get(
                        "access-control-expose-headers", ""
                    )
                    to_add = ["traceparent", "X-Request-Id", "tracestate"]
                    merged = ", ".join(
                        [existing_expose] + to_add if existing_expose else to_add
                    ).strip(", ")
                    response.headers[
                        "access-control-expose-headers"
                    ] = merged
                except Exception:
                    pass

            logger.info(
                "trace.request.finished",
                route=route,
                method=method,
                status=status_code,
                duration_ms=round(duration * 1_000, 2),
            )

            if scope_token is not None:
                reset_context(scope_token)
            reset_trace(trace_token)

        return response  # type: ignore[return-value]


def install(app: FastAPI) -> None:
    """Register the middleware + health/metrics routes on ``app``."""

    app.add_middleware(ObservabilityMiddleware)
    # Avoid circular import; health uses metrics, middleware doesn't use health.
    from app.observability.health import router as health_router

    app.include_router(health_router)


__all__ = ["ObservabilityMiddleware", "install"]
