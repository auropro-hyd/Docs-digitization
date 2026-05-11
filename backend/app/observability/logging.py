"""Structured logging configuration.

One call — :func:`configure` — wires structlog as the primary logger for
application code AND as the renderer for the stdlib root logger, so existing
``logging.getLogger(__name__)`` call sites keep working unchanged while gaining
context enrichment + redaction + mode-aware output.

Processors, in order:

1. ``add_log_level`` — translate ``info``/``warn``/… to a ``level`` field.
2. ``TimeStamper(fmt="iso")`` — ISO-8601 UTC timestamp on every record.
3. :func:`_inject_trace_processor` — lift ``trace_id`` / ``span_id`` /
   ``parent_span_id`` out of the :class:`TraceContext` ContextVar.
4. :func:`_inject_scope_processor` — lift ``actor_id`` / ``doc_id`` / … out
   of the :class:`RequestScope` ContextVar.
5. ``format_exc_info`` — render ``exc_info`` into an ``error.stack`` string.
6. :func:`redact_processor` — redact oversized / binary-looking values.
7. ``JSONRenderer`` (prod) or ``ConsoleRenderer`` (dev) — final line.

Mode is driven by the ``AT_OBS__LOG_MODE`` env var (``json`` / ``dev``).
Level by ``AT_OBS__LOG_LEVEL``. Both have sane defaults.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog

from app.observability.context import current_scope, current_trace
from app.observability.redaction import redact_processor


def _inject_trace_processor(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    ctx = current_trace()
    if ctx is not None:
        event_dict.setdefault("trace_id", ctx.trace_id)
        event_dict.setdefault("span_id", ctx.span_id)
        if ctx.parent_span_id is not None:
            event_dict.setdefault("parent_span_id", ctx.parent_span_id)
    return event_dict


def _inject_scope_processor(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    for k, v in current_scope().as_dict().items():
        event_dict.setdefault(k, v)
    return event_dict


def _telemetry_capture_processor(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Mirror every log record into the active ``RunTelemetrySink``.

    Lazy import so a circular logging↔telemetry dependency can't
    deadlock startup. No-op when no sink is bound (the common case
    outside of a pipeline run, including tests that don't enter
    ``telemetry_run``).

    Never raises into the structlog pipeline — telemetry capture
    is a side-effect, not a correctness surface.
    """
    try:
        from app.observability.run_telemetry import current_sink

        sink = current_sink()
        if sink is None:
            return event_dict

        logger_name = ""
        if logger is not None:
            logger_name = getattr(logger, "name", "") or ""
        # Pull the structured fields the user passed, excluding the
        # framework keys we've already serialized.
        framework_keys = {
            "event", "ts", "level", "trace_id", "span_id",
            "parent_span_id", "logger", "stack_info", "exc_info",
        }
        fields = {k: v for k, v in event_dict.items() if k not in framework_keys}

        sink.record(
            event=str(event_dict.get("event", "log")),
            level=str(event_dict.get("level", method_name)),
            logger_name=logger_name,
            **fields,
        )
    except Exception:  # pragma: no cover — never break logging
        pass
    return event_dict


def _resolve_log_mode() -> str:
    mode = os.getenv("AT_OBS__LOG_MODE", "").strip().lower()
    if mode in {"json", "dev"}:
        return mode
    # Default to dev when stdout is a TTY, json otherwise — matches normal
    # Python observability conventions.
    return "dev" if sys.stdout.isatty() else "json"


def _resolve_log_level() -> int:
    raw = os.getenv("AT_OBS__LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, raw, logging.INFO)


_CONFIGURED = False


def configure(*, force: bool = False) -> None:
    """Configure structlog + stdlib logging. Idempotent unless ``force``."""

    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    mode = _resolve_log_mode()
    level = _resolve_log_level()

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        _inject_trace_processor,
        _inject_scope_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_processor,
        # Capture every log record into the active run telemetry
        # sink. No-op when no sink is bound. Lives AFTER redaction
        # so the on-disk telemetry doesn't carry secrets that the
        # redaction processor was meant to strip.
        _telemetry_capture_processor,
    ]

    if mode == "json":
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Stdlib bridge — replace the root handler so `logging.getLogger(...)`
    # routes through the same processor chain.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=shared_processors,
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Keep a couple of very chatty SDKs quiet — same entries that main.py
    # used to set manually. This lets us remove that basicConfig block.
    for noisy in (
        "azure.core.pipeline.policies.http_logging_policy",
        "azure.ai.documentintelligence",
        "httpx",
        "httpcore",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog-bound logger. Auto-configures if not yet set up."""

    if not _CONFIGURED:
        configure()
    return structlog.stdlib.get_logger(name)


__all__ = ["configure", "get_logger"]
