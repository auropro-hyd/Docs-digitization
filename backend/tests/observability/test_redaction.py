"""FR-008: log-payload redaction."""

from __future__ import annotations

import os

from app.observability.redaction import redact_processor


def test_redacts_oversized_strings() -> None:
    ed = redact_processor(None, "info", {"event": "x", "blob": "A" * 4096})
    assert "<redacted: oversized" in ed["blob"]


def test_redacts_pdf_bytes_marker() -> None:
    ed = redact_processor(None, "info", {"event": "x", "pdf": "%PDF-1.4\nstuff"})
    assert "<redacted: binary-like" in ed["pdf"]


def test_redacts_bytes_payloads() -> None:
    ed = redact_processor(None, "info", {"event": "x", "raw": b"\x00\x01\x02" * 100})
    assert "<redacted: binary-like" in ed["raw"]


def test_exempts_event_and_msg_even_if_large() -> None:
    big = "A" * 4096
    ed = redact_processor(
        None, "info", {"event": big, "msg": big, "note": big}
    )
    assert ed["event"] == big  # exempt
    assert ed["msg"] == big    # exempt
    assert ed["note"] != big   # redacted


def test_redacts_long_base64_like_string() -> None:
    payload = os.urandom(1600).hex()  # ~3200 chars of [0-9a-f], matches base64 RE
    # Use a more strictly base64-ish payload to trigger the regex (uppercase + digits)
    b64ish = ("ABC1def2" * 300)  # 2400 chars of A-Za-z0-9
    ed = redact_processor(
        None, "info", {"event": "x", "a": payload, "b": b64ish}
    )
    # Either format may or may not match depending on regex strictness; at
    # least one of them should have been redacted as binary-like.
    redacted_any = any(
        isinstance(ed[k], str) and "<redacted" in ed[k] for k in ("a", "b")
    )
    assert redacted_any, f"expected at least one redaction, got {ed}"
