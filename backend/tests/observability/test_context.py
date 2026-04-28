"""Context bind/reset + scope-key validation + cross-await inheritance."""

from __future__ import annotations

import asyncio

import pytest

from app.observability.context import (
    TraceContext,
    bind_context,
    current_scope,
    current_trace,
    reset_context,
    set_trace,
)


def test_bind_context_merges_and_resets() -> None:
    assert current_scope().doc_id is None
    t = bind_context(doc_id="D1")
    try:
        assert current_scope().doc_id == "D1"
        t2 = bind_context(run_id="R1")
        try:
            scope = current_scope()
            assert scope.doc_id == "D1"
            assert scope.run_id == "R1"
        finally:
            reset_context(t2)
        assert current_scope().run_id is None
        assert current_scope().doc_id == "D1"
    finally:
        reset_context(t)
    assert current_scope().doc_id is None


def test_bind_context_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="unknown scope keys"):
        bind_context(user_email="spoof@evil")


def test_trace_context_rejects_zero_ids() -> None:
    with pytest.raises(ValueError):
        TraceContext(trace_id="0" * 32, span_id="1" * 16)
    with pytest.raises(ValueError):
        TraceContext(trace_id="1" * 32, span_id="0" * 16)


def test_trace_context_inherits_across_await() -> None:
    async def inner() -> str | None:
        ctx = current_trace()
        return ctx.trace_id if ctx else None

    async def outer() -> str | None:
        tok = set_trace(TraceContext(trace_id="a" * 32, span_id="b" * 16))
        try:
            return await inner()
        finally:
            from app.observability.context import reset_trace

            reset_trace(tok)

    result = asyncio.run(outer())
    assert result == "a" * 32
