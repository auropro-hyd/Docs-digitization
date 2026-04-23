"""Log-payload redaction processor.

Defence against the catastrophic "ship raw PDF bytes / LLM prompts / OCR text
into a log pipeline" failure mode. Caller never sees the redaction happen —
the processor replaces oversized / binary-looking values with a marker and
increments ``errors_total{kind="LogRedaction"}`` so the misuse is detectable.
"""

from __future__ import annotations

import re
from typing import Any

_MAX_VALUE_BYTES = 2 * 1024  # 2 KiB
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=]{2048,}$")

# Keys that are allowed to exceed the default size cap because they are
# structured + low-risk. ``error.stack`` is produced by the logger itself.
_EXEMPT_KEYS = frozenset(
    {"event", "msg", "error.stack", "error_stack", "stack"}
)


def _looks_binary(value: str) -> bool:
    if value.startswith("%PDF"):
        return True
    return bool(len(value) >= 2048 and _BASE64_RE.match(value))


def redact_processor(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor — in-place redaction of large / binary values."""

    redacted = 0
    for key, value in list(event_dict.items()):
        if key in _EXEMPT_KEYS:
            continue
        if isinstance(value, (bytes, bytearray)):
            event_dict[key] = f"<redacted: binary-like ({len(value)} bytes)>"
            redacted += 1
            continue
        if isinstance(value, str):
            encoded_size = len(value.encode("utf-8", errors="replace"))
            if encoded_size > _MAX_VALUE_BYTES:
                event_dict[key] = (
                    f"<redacted: oversized ({encoded_size} bytes)>"
                )
                redacted += 1
                continue
            if _looks_binary(value):
                event_dict[key] = "<redacted: binary-like>"
                redacted += 1
    if redacted:
        # Best-effort — metrics may not yet be configured during very early
        # startup logging, in which case the import is a no-op counter.
        try:
            from app.observability.metrics import LOG_REDACTIONS

            LOG_REDACTIONS.labels(kind="LogRedaction").inc(redacted)
        except Exception:  # pragma: no cover — fail-open
            pass
    return event_dict


__all__ = ["redact_processor"]
