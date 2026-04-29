"""Tests for DatalabOCRAdapter's fail-fast key validation + 401 bail.

The original failure mode this guards against: a bad API key produced
hours of "OCR progress 0% — analyzing" labels with 401 errors buried
in the warning log. Two mechanisms now stop that:

1. Construction-time validation (mirrors the Gemini adapter pattern)
   so a missing / placeholder / suspiciously-short key fails the
   service at startup with an actionable message.
2. Bail-fast on 401/403 from the API — these are not transient and
   retrying them just produces misleading progress.

Both are pinned here so a future refactor that softens either guard
gets caught at CI rather than at the next 6-hour stuck-bar incident.
"""

from __future__ import annotations

import pytest

from app.adapters.ocr.datalab import DatalabOCRAdapter
from app.config.settings import DatalabConfig


# ── construction-time validation ────────────────────────────────────────────


def test_empty_api_key_raises_with_actionable_message() -> None:
    cfg = DatalabConfig(api_key="")
    with pytest.raises(RuntimeError, match=r"AT_DATALAB__api_key is empty"):
        DatalabOCRAdapter(cfg)


def test_placeholder_api_key_is_rejected() -> None:
    """Common copy-paste placeholders must not silently pass — the
    operator gets a clear "you forgot to replace it" error rather
    than a 401 from the API hours later."""

    for placeholder in ("your-api-key-here", "REPLACE_ME", "<your-key>"):
        with pytest.raises(RuntimeError, match=r"placeholder"):
            DatalabOCRAdapter(DatalabConfig(api_key=placeholder))


def test_too_short_api_key_is_rejected() -> None:
    """A truncated paste is the second-most-common bad-key cause."""

    with pytest.raises(RuntimeError, match=r"suspiciously short"):
        DatalabOCRAdapter(DatalabConfig(api_key="short"))


def test_api_key_quotes_are_stripped() -> None:
    """``.env`` files sometimes carry stray quotes that the API treats
    as malformed. The adapter strips them so a key wrapped in
    \"...\" or '...' loads correctly without operator intervention."""

    cfg = DatalabConfig(api_key='"a-real-looking-key-1234567890"')
    adapter = DatalabOCRAdapter(cfg)
    assert adapter._cleaned_api_key == "a-real-looking-key-1234567890"


# ── 401 bail-fast ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_401_raises_immediately_without_retrying() -> None:
    """A 401 from the API must fail the run on the first attempt.

    Without this gate, ``submit_max_retries`` (default 5) × exponential
    backoff (5s, 10s, 20s, …) means a misconfigured key keeps the run
    "alive" for ~10 minutes per chunk while emitting heartbeat labels
    that imply progress. The bar can't move because nothing
    completes; the user sees nothing actionable. Bailing immediately
    surfaces the auth error to the run report where it belongs.
    """

    from datalab_sdk.exceptions import DatalabAPIError

    cfg = DatalabConfig(
        api_key="a-real-looking-key-1234567890",
        submit_max_retries=5,
        submit_retry_base_delay=0.01,
    )
    adapter = DatalabOCRAdapter(cfg)

    call_count = 0

    class _UnauthorizedClient:
        async def convert(self, **_kwargs):
            nonlocal call_count
            call_count += 1
            raise DatalabAPIError("Unauthorized", status_code=401)

    adapter._client = _UnauthorizedClient()

    with pytest.raises(RuntimeError, match=r"401 Unauthorized.*Get a fresh key"):
        await adapter._submit_with_retry(
            "/tmp/fake.pdf",
            "0-9",
            progress_callback=None,
        )

    assert call_count == 1, (
        f"401 must bail on the first attempt; got {call_count} calls — "
        "the adapter is still treating auth failures as transient"
    )


@pytest.mark.asyncio
async def test_403_also_bails_fast() -> None:
    """403 (forbidden / over-quota) is the same class of error as 401
    from a retry perspective — never recovers without operator
    action. Pinning it explicitly so a future refactor that narrows
    the bail to 401-only gets caught here."""

    from datalab_sdk.exceptions import DatalabAPIError

    cfg = DatalabConfig(
        api_key="a-real-looking-key-1234567890",
        submit_max_retries=5,
        submit_retry_base_delay=0.01,
    )
    adapter = DatalabOCRAdapter(cfg)

    class _ForbiddenClient:
        async def convert(self, **_kwargs):
            raise DatalabAPIError("Forbidden", status_code=403)

    adapter._client = _ForbiddenClient()

    with pytest.raises(RuntimeError, match=r"403 Unauthorized"):
        await adapter._submit_with_retry(
            "/tmp/fake.pdf", "0-9", progress_callback=None,
        )


@pytest.mark.asyncio
async def test_5xx_still_retries() -> None:
    """Server-side / transient errors should still be retried —
    a 503 during a deploy or a 502 from a momentary upstream blip
    can recover. Only auth-class errors (401/403) bail."""

    from datalab_sdk.exceptions import DatalabAPIError

    cfg = DatalabConfig(
        api_key="a-real-looking-key-1234567890",
        submit_max_retries=3,
        submit_retry_base_delay=0.01,
    )
    adapter = DatalabOCRAdapter(cfg)

    call_count = 0

    class _FlakyClient:
        async def convert(self, **_kwargs):
            nonlocal call_count
            call_count += 1
            raise DatalabAPIError("Bad gateway", status_code=502)

    adapter._client = _FlakyClient()

    with pytest.raises(RuntimeError, match=r"failed after 3 attempts"):
        await adapter._submit_with_retry(
            "/tmp/fake.pdf", "0-9", progress_callback=None,
        )

    assert call_count == 3, (
        f"transient 5xx must be retried up to ``submit_max_retries`` times; "
        f"got {call_count} calls"
    )
