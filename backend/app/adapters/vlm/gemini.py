"""Google Gemini VLM adapter.

Uses the ``google-genai`` SDK to call Gemini models with image inputs
and optional structured JSON output.  Includes rate limiting and
exponential-backoff retry for transient errors.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time

from pydantic import BaseModel

from app.config.settings import VLMConfig

logger = logging.getLogger(__name__)

_MAX_RETRIES = 4
_BASE_DELAY = 1.0
_MAX_DELAY = 30.0


def _resize_image_if_needed(
    image: bytes, max_w: int, max_h: int, fmt: str = "PNG",
) -> bytes:
    """Down-scale *image* so it fits within *max_w* x *max_h*."""
    try:
        from PIL import Image
    except ImportError:
        return image

    img = Image.open(io.BytesIO(image))
    if img.width <= max_w and img.height <= max_h:
        return image

    img.thumbnail((max_w, max_h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


class GeminiVLMAdapter:
    """Gemini adapter implementing the ``VLMProvider`` protocol."""

    def __init__(self, config: VLMConfig) -> None:
        self._config = config
        self._model = config.gemini_model
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        self._rpm_timestamps: list[float] = []
        self._lock = asyncio.Lock()

        # Trim — env loaders sometimes leave trailing whitespace or quotes
        # that the API treats as a malformed key.
        api_key = (config.gemini_api_key or "").strip().strip('"').strip("'")
        if not api_key:
            # Fail fast at construction with an actionable message instead
            # of letting every downstream call fail with the opaque
            # "API key not valid" 400. The user has reported exactly this
            # symptom when AT_VLM__GEMINI_API_KEY is unset or .env is not
            # being picked up due to a working-directory mismatch.
            raise RuntimeError(
                "GeminiVLMAdapter: gemini_api_key is empty. "
                "Set AT_VLM__GEMINI_API_KEY in backend/.env (and verify the file is "
                "loaded — uvicorn must be invoked so that pydantic-settings can find "
                "the absolute backend/.env path)."
            )
        # Detect a copied placeholder so the operator sees the real cause.
        if api_key.startswith(("your-", "REPLACE", "<")) or api_key.endswith(("-here", ">")):
            raise RuntimeError(
                f"GeminiVLMAdapter: gemini_api_key looks like a placeholder ({api_key[:8]}…). "
                "Replace it with a real key from Google AI Studio."
            )
        if len(api_key) < 20:
            raise RuntimeError(
                f"GeminiVLMAdapter: gemini_api_key is suspiciously short ({len(api_key)} chars). "
                "Verify the key was copied in full and is not truncated."
            )

        from google import genai

        self._client = genai.Client(api_key=api_key)
        logger.info(
            "Gemini VLM adapter ready: model=%s key=***%s (length=%d)",
            self._model,
            api_key[-4:],
            len(api_key),
        )

    # ------------------------------------------------------------------
    # Rate limiter (simplified version of AzureRateLimiter)
    # ------------------------------------------------------------------

    async def _acquire(self) -> None:
        await self._semaphore.acquire()
        async with self._lock:
            now = time.monotonic()
            cutoff = now - 60.0
            self._rpm_timestamps = [t for t in self._rpm_timestamps if t > cutoff]
            while len(self._rpm_timestamps) >= self._config.max_rpm:
                oldest = self._rpm_timestamps[0]
                wait = (oldest + 60.0) - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                now = time.monotonic()
                cutoff = now - 60.0
                self._rpm_timestamps = [t for t in self._rpm_timestamps if t > cutoff]
            self._rpm_timestamps.append(time.monotonic())

    def _release(self) -> None:
        self._semaphore.release()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze_image(
        self,
        image: bytes,
        prompt: str,
        *,
        system: str | None = None,
        mime_type: str = "image/png",
    ) -> str:
        image = _resize_image_if_needed(
            image, self._config.max_image_width, self._config.max_image_height,
        )
        contents = self._build_contents(image, prompt, mime_type)
        config = self._build_config(system=system)

        return await self._call(contents, config)

    async def analyze_image_structured(
        self,
        image: bytes,
        prompt: str,
        schema: type[BaseModel],
        *,
        system: str | None = None,
        mime_type: str = "image/png",
    ) -> BaseModel:
        image = _resize_image_if_needed(
            image, self._config.max_image_width, self._config.max_image_height,
        )
        contents = self._build_contents(image, prompt, mime_type)
        config = self._build_config(system=system, schema=schema)

        raw = await self._call(contents, config)
        parsed = json.loads(raw)
        return schema.model_validate(parsed)

    def supports_structured_output(self) -> bool:
        return True

    def max_image_resolution(self) -> tuple[int, int]:
        return (self._config.max_image_width, self._config.max_image_height)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_contents(self, image: bytes, prompt: str, mime_type: str) -> list:
        from google.genai import types

        return [
            types.Part.from_bytes(data=image, mime_type=mime_type),
            prompt,
        ]

    def _build_config(
        self, *, system: str | None = None, schema: type[BaseModel] | None = None,
    ) -> dict:
        from google.genai import types

        cfg: dict = {}
        if system:
            cfg["system_instruction"] = system
        if schema is not None:
            cfg["response_mime_type"] = "application/json"
            cfg["response_schema"] = schema
        return cfg

    async def _call(self, contents: list, config: dict) -> str:
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            await self._acquire()
            try:
                response = await asyncio.to_thread(
                    self._client.models.generate_content,
                    model=self._model,
                    contents=contents,
                    config=config,
                )
                return response.text
            except Exception as exc:
                last_exc = exc
                err_str = str(exc).lower()
                retryable = any(k in err_str for k in ("429", "503", "resource_exhausted", "overloaded"))
                if not retryable or attempt == _MAX_RETRIES:
                    raise
                delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
                logger.warning(
                    "Gemini VLM attempt %d failed (retryable): %s — retrying in %.1fs",
                    attempt + 1, exc, delay,
                )
                await asyncio.sleep(delay)
            finally:
                self._release()

        raise last_exc  # type: ignore[misc]
