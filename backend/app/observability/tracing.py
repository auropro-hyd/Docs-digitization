"""Trace id parsing / minting + span helpers + executor-context bridge.

Implements the contract in
``specs/006-observability-and-finding-semantics/contracts/trace-header-contract.md``.
The public API is narrow:

* :func:`parse_traceparent` / :func:`mint_trace` — ingest and origin.
* :func:`span` — child-span context manager.
* :func:`traced` — decorator around a function that auto-spans it.
* :func:`submit_with_context` — run a callable on an executor while carrying
  the caller's ``ContextVar`` state along.
"""

from __future__ import annotations

import functools
import re
import secrets
from collections.abc import Callable
from concurrent.futures import Executor, Future
from contextlib import contextmanager
from contextvars import copy_context
from typing import Any, ParamSpec, TypeVar

from app.observability.context import (
    TraceContext,
    current_trace,
    reset_trace,
    set_trace,
)

_TRACEPARENT_RE = re.compile(
    r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$"
)
_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def parse_traceparent(value: str | None) -> TraceContext | None:
    """Parse a W3C Trace Context header. Return ``None`` on any defect.

    Callers log a ``trace.malformed_header`` warning on ``None`` and fall
    back to :func:`mint_trace`.
    """

    if not value:
        return None
    m = _TRACEPARENT_RE.match(value.strip())
    if m is None:
        return None
    trace_id, parent_id, flags = m.group(1), m.group(2), m.group(3)
    if trace_id == "0" * 32 or parent_id == "0" * 16:
        return None
    try:
        return TraceContext(
            trace_id=trace_id,
            span_id=parent_id,
            parent_span_id=None,
            flags=flags,
        )
    except ValueError:
        return None


def try_from_request_id(value: str | None) -> TraceContext | None:
    """Use a bare hex ``X-Request-Id`` as ``trace_id`` if it parses."""

    if value is None or not _REQUEST_ID_RE.match(value.strip()):
        return None
    return TraceContext(
        trace_id=value.strip().lower(),
        span_id=secrets.token_hex(8),
    )


def mint_trace() -> TraceContext:
    """Mint a fresh, sampled trace context."""

    return TraceContext(
        trace_id=secrets.token_hex(16),
        span_id=secrets.token_hex(8),
    )


def _new_span_id() -> str:
    return secrets.token_hex(8)


@contextmanager
def span(name: str) -> Any:
    """Push a child span onto ``TRACE_CTX`` for the duration of the block.

    The ``name`` is purely informational for now (used by logs via the
    context processor). When we swap in the OTEL SDK, this is where a
    real ``Tracer.start_as_current_span`` call will live.
    """

    parent = current_trace()
    if parent is None:
        # No inbound trace — synthesize a root so logs still correlate.
        parent = mint_trace()
        token = set_trace(parent)
        try:
            yield parent
        finally:
            reset_trace(token)
        return

    child = parent.child_span(_new_span_id())
    token = set_trace(child)
    try:
        yield child
    finally:
        reset_trace(token)


P = ParamSpec("P")
R = TypeVar("R")


def traced(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Wrap a callable in a :func:`span` with ``name``.

    ``@traced("bmr.stage.ingest")`` is the expected usage. Sync and async
    functions both supported.
    """

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        import asyncio

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                with span(name):
                    return await fn(*args, **kwargs)  # type: ignore[return-value]

            return awrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with span(name):
                return fn(*args, **kwargs)

        return wrapper

    return decorator


def submit_with_context(  # noqa: UP047 — keep ParamSpec form for cross-version clarity
    executor: Executor,
    fn: Callable[P, R],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> Future[R]:
    """``executor.submit(fn, *args, **kwargs)`` that carries the current
    ``contextvars`` context along.

    Required for any ``ThreadPoolExecutor`` usage that wants trace ids /
    request scope to survive into the worker thread.
    """

    ctx = copy_context()

    def _run() -> R:
        return ctx.run(fn, *args, **kwargs)

    return executor.submit(_run)


__all__ = [
    "mint_trace",
    "parse_traceparent",
    "span",
    "submit_with_context",
    "traced",
    "try_from_request_id",
]
