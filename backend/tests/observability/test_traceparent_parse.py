"""traceparent parse + mint contract (contracts/trace-header-contract.md)."""

from __future__ import annotations

import pytest

from app.observability.tracing import (
    mint_trace,
    parse_traceparent,
    try_from_request_id,
)


def test_parse_valid_header() -> None:
    h = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    ctx = parse_traceparent(h)
    assert ctx is not None
    assert ctx.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert ctx.span_id == "00f067aa0ba902b7"
    assert ctx.flags == "01"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        None,
        "garbage",
        "00-0000000000000000000000000000000000-00f067aa0ba902b7-01",  # 34 chars trace_id
        "00-" + "0" * 32 + "-00f067aa0ba902b7-01",                     # all-zero trace_id
        "00-4bf92f3577b34da6a3ce929d0e0e4736-" + "0" * 16 + "-01",     # all-zero span_id
        "01-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",     # wrong version
        "00-XXXX2f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",     # non-hex
    ],
)
def test_parse_rejects_bad_inputs(bad: str | None) -> None:
    assert parse_traceparent(bad) is None


def test_mint_trace_produces_valid_ids() -> None:
    ctx = mint_trace()
    assert len(ctx.trace_id) == 32
    assert len(ctx.span_id) == 16
    assert ctx.trace_id != "0" * 32
    assert ctx.span_id != "0" * 16
    # Round-trip: the minted ctx serialises and reparses to an equivalent.
    round = parse_traceparent(ctx.to_header())
    assert round is not None
    assert round.trace_id == ctx.trace_id
    assert round.span_id == ctx.span_id


def test_try_from_request_id_accepts_hex_only() -> None:
    good = "4bf92f3577b34da6a3ce929d0e0e4736"
    ctx = try_from_request_id(good)
    assert ctx is not None
    assert ctx.trace_id == good

    for bad in (None, "", "not-hex", "4bf92f35"):
        assert try_from_request_id(bad) is None
