"""vLLM / OpenAI-compatible VLM adapter.

Connects to any VLM served behind an OpenAI-compatible API (vLLM,
LMDeploy, TGI, etc.).  Images are sent as base64-encoded ``image_url``
content parts.  Structured output uses ``response_format``.
"""

from __future__ import annotations

import asyncio
import base64
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


class VLLMOpenAIVLMAdapter:
    """OpenAI-compatible VLM adapter implementing the ``VLMProvider`` protocol."""

    def __init__(self, config: VLMConfig) -> None:
        self._config = config
        self._model = config.vllm_model
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        self._rpm_timestamps: list[float] = []
        self._lock = asyncio.Lock()

        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            base_url=config.vllm_base_url,
            api_key=config.vllm_api_key or "EMPTY",
        )

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

    def _image_to_data_url(self, image: bytes, mime_type: str) -> str:
        b64 = base64.b64encode(image).decode()
        return f"data:{mime_type};base64,{b64}"

    def _build_messages(
        self,
        image: bytes,
        prompt: str,
        mime_type: str,
        system: str | None = None,
    ) -> list[dict]:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": self._image_to_data_url(image, mime_type)},
                },
                {"type": "text", "text": prompt},
            ],
        })
        return messages

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
        messages = self._build_messages(image, prompt, mime_type, system)
        return await self._call(messages)

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
        messages = self._build_messages(image, prompt, mime_type, system)
        raw = await self._call(messages, schema=schema)
        parsed = json.loads(raw)
        return schema.model_validate(parsed)

    def supports_structured_output(self) -> bool:
        return True

    def max_image_resolution(self) -> tuple[int, int]:
        return (self._config.max_image_width, self._config.max_image_height)

    async def _call(
        self,
        messages: list[dict],
        schema: type[BaseModel] | None = None,
    ) -> str:
        kwargs: dict = {
            "model": self._model,
            "messages": messages,
            "max_tokens": 4096,
        }
        if schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                },
            }

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            await self._acquire()
            try:
                response = await self._client.chat.completions.create(**kwargs)
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_exc = exc
                err_str = str(exc).lower()
                retryable = any(k in err_str for k in ("429", "503", "overloaded", "rate"))
                if not retryable or attempt == _MAX_RETRIES:
                    raise
                delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
                logger.warning(
                    "vLLM VLM attempt %d failed (retryable): %s — retrying in %.1fs",
                    attempt + 1, exc, delay,
                )
                await asyncio.sleep(delay)
            finally:
                self._release()

        raise last_exc  # type: ignore[misc]
