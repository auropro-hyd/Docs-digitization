"""Per-request context (trace + business scope) propagated via contextvars.

Two ``ContextVar`` carry the state. They inherit across ``await`` and, via
``contextvars.copy_context()``, across worker threads that the rest of the
observability package wraps for us (see :mod:`app.observability.tracing`).

* ``TRACE_CTX``    — the W3C trace id + active span id for this unit of work.
* ``REQUEST_SCOPE`` — a small, closed set of business ids (``doc_id``,
  ``run_id``, ``stage``, ``agent``, ``rule_id``, ``actor_id``).

Binding is done through :func:`bind_context` — it refuses unknown keys so a
typo cannot silently proliferate new scope fields.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from typing import Any

_SCOPE_KEYS = frozenset(
    {"actor_id", "doc_id", "run_id", "stage", "agent", "rule_id"}
)


@dataclass(frozen=True, slots=True)
class TraceContext:
    """W3C Trace Context (v0 / version = ``00``).

    See ``specs/006-observability-and-finding-semantics/contracts/trace-header-contract.md``
    for the exact parse + emission rules. Constructors enforce the
    non-zero-id invariant; unsafe callers receive ``ValueError``.
    """

    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    flags: str = "01"
    tracestate: str = ""
    version: str = "00"

    def __post_init__(self) -> None:
        if len(self.trace_id) != 32 or self.trace_id == "0" * 32:
            raise ValueError(f"invalid trace_id: {self.trace_id!r}")
        if len(self.span_id) != 16 or self.span_id == "0" * 16:
            raise ValueError(f"invalid span_id: {self.span_id!r}")

    def child_span(self, new_span_id: str) -> TraceContext:
        """Return a new context whose ``span_id`` is ``new_span_id`` and whose
        ``parent_span_id`` is the current ``span_id``.

        Callers construct the span id via :mod:`app.observability.tracing`.
        """

        return replace(
            self,
            span_id=new_span_id,
            parent_span_id=self.span_id,
        )

    def to_header(self) -> str:
        return f"{self.version}-{self.trace_id}-{self.span_id}-{self.flags}"


@dataclass(frozen=True, slots=True)
class RequestScope:
    """Business ids attached to a request. All optional — ``None`` = unknown."""

    actor_id: str | None = None
    doc_id: str | None = None
    run_id: str | None = None
    stage: str | None = None
    agent: str | None = None
    rule_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in {
                "actor_id": self.actor_id,
                "doc_id": self.doc_id,
                "run_id": self.run_id,
                "stage": self.stage,
                "agent": self.agent,
                "rule_id": self.rule_id,
            }.items()
            if v is not None
        }


TRACE_CTX: ContextVar[TraceContext | None] = ContextVar("TRACE_CTX", default=None)
# RequestScope is frozen + slots=True (immutable), so sharing one default
# across requests is safe — the only way to "modify" scope is to ``replace``
# which yields a new instance. ruff's B039 assumes mutability; silence it
# explicitly.
REQUEST_SCOPE: ContextVar[RequestScope] = ContextVar(
    "REQUEST_SCOPE",
    default=RequestScope(),  # noqa: B039 — RequestScope is frozen
)


def current_trace() -> TraceContext | None:
    return TRACE_CTX.get()


def current_scope() -> RequestScope:
    return REQUEST_SCOPE.get()


@dataclass(frozen=True, slots=True)
class _Bindings:
    trace_token: Token[TraceContext | None] | None = None
    scope_token: Token[RequestScope] | None = None


def set_trace(ctx: TraceContext | None) -> Token[TraceContext | None]:
    return TRACE_CTX.set(ctx)


def reset_trace(token: Token[TraceContext | None]) -> None:
    TRACE_CTX.reset(token)


def bind_context(**fields: Any) -> Token[RequestScope]:
    """Merge ``fields`` into the current :class:`RequestScope` and set a new
    snapshot on :data:`REQUEST_SCOPE`. Returns a ``Token`` callers must pass
    to :func:`reset_context` to unwind (typically in a ``finally``).

    Unknown keys raise ``ValueError`` — the scope dict is deliberately small
    and the catalogue lives in ``_SCOPE_KEYS``.
    """

    unknown = set(fields) - _SCOPE_KEYS
    if unknown:
        raise ValueError(
            f"unknown scope keys: {sorted(unknown)!r}; allowed={sorted(_SCOPE_KEYS)!r}"
        )
    current = REQUEST_SCOPE.get()
    merged = replace(current, **fields)
    return REQUEST_SCOPE.set(merged)


def reset_context(token: Token[RequestScope]) -> None:
    REQUEST_SCOPE.reset(token)


__all__ = [
    "REQUEST_SCOPE",
    "TRACE_CTX",
    "RequestScope",
    "TraceContext",
    "bind_context",
    "current_scope",
    "current_trace",
    "reset_context",
    "reset_trace",
    "set_trace",
]
