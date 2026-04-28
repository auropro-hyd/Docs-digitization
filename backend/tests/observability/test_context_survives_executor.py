"""FR-002: trace id + request scope must survive ThreadPoolExecutor."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.observability.context import (
    TraceContext,
    bind_context,
    current_scope,
    current_trace,
    reset_context,
    reset_trace,
    set_trace,
)
from app.observability.tracing import submit_with_context


def _worker_reads_ctx() -> tuple[str | None, str | None]:
    ctx = current_trace()
    scope = current_scope()
    return (ctx.trace_id if ctx else None, scope.doc_id)


def test_submit_with_context_carries_trace_and_scope() -> None:
    ctx = TraceContext(trace_id="a" * 32, span_id="b" * 16)
    t_trace = set_trace(ctx)
    t_scope = bind_context(doc_id="doc-xyz")
    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut = submit_with_context(ex, _worker_reads_ctx)
            got_trace, got_doc = fut.result(timeout=2)
    finally:
        reset_context(t_scope)
        reset_trace(t_trace)

    assert got_trace == "a" * 32
    assert got_doc == "doc-xyz"


def test_plain_submit_does_NOT_carry_context() -> None:
    """Sanity — documents why submit_with_context is needed."""

    ctx = TraceContext(trace_id="c" * 32, span_id="d" * 16)
    t = set_trace(ctx)
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            # Plain submit — no context copy.
            fut = ex.submit(_worker_reads_ctx)
            got_trace, _ = fut.result(timeout=2)
    finally:
        reset_trace(t)

    assert got_trace != "c" * 32
