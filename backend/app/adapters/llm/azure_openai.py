"""Azure OpenAI LLM adapter.

Supports both prompt-based generation and native structured output
via the ``response_format`` API parameter (GPT-4.1+ / GPT-5 models).

Includes exponential-backoff retry for transient 429 rate-limit errors.
Enforces per-deployment RPM/concurrency via a shared semaphore and rate limiter.
When a 429 is received, the backoff window is propagated globally so all
concurrent callers wait instead of burning more RPM quota.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time

import httpx
from pydantic import BaseModel

from app.config.settings import LLMConfig

logger = logging.getLogger(__name__)

_API_VERSION = "2024-12-01-preview"
_MAX_RETRIES = 5
_BASE_DELAY = 1.0
_MAX_DELAY = 60.0
_WINDOW_SEC = 60.0
_MIN_REQUEST_INTERVAL_S = 0.25  # minimum gap between any two HTTP requests (seconds)

_limiter_registry: dict[tuple[str, str], "AzureRateLimiter"] = {}


class AzureRateLimiter:
    """Enforces max RPM (sliding window) + max concurrent + global 429 backoff."""

    def __init__(self, max_rpm: int, max_concurrent: int) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_rpm = max_rpm
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()
        self._blocked_until: float = 0.0

    async def acquire(self) -> None:
        """Wait for semaphore, global backoff, inter-request spacing, and RPM window."""
        await self._semaphore.acquire()
        async with self._lock:
            now = time.monotonic()

            # 1) Global 429 backoff
            if self._blocked_until > now:
                wait = self._blocked_until - now
                logger.info("Global backoff: waiting %.1fs before next request", wait)
                await asyncio.sleep(wait)

            # 2) Minimum inter-request spacing to prevent token-burst 429s
            if self._timestamps:
                since_last = time.monotonic() - self._timestamps[-1]
                gap = _MIN_REQUEST_INTERVAL_S - since_last
                if gap > 0:
                    await asyncio.sleep(gap)

            # 3) RPM sliding-window check
            now = time.monotonic()
            cutoff = now - _WINDOW_SEC
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            while len(self._timestamps) >= self._max_rpm:
                oldest = self._timestamps[0]
                wait_for = (oldest + _WINDOW_SEC) - time.monotonic()
                if wait_for > 0:
                    await asyncio.sleep(wait_for)
                now = time.monotonic()
                cutoff = now - _WINDOW_SEC
                self._timestamps = [t for t in self._timestamps if t > cutoff]
            self._timestamps.append(time.monotonic())

    def record_request(self) -> None:
        """Record an additional HTTP request (e.g. retry) in the sliding window."""
        self._timestamps.append(time.monotonic())

    def report_429(self, retry_after: float) -> None:
        """Set a global backoff so all callers wait instead of burning more quota."""
        blocked = time.monotonic() + retry_after
        if blocked > self._blocked_until:
            self._blocked_until = blocked

    def release(self) -> None:
        self._semaphore.release()


def _get_limiter(endpoint: str, deployment: str, config: LLMConfig) -> AzureRateLimiter:
    """Get or create a shared rate limiter for this deployment."""
    key = (endpoint.rstrip("/"), deployment)
    if key not in _limiter_registry:
        _limiter_registry[key] = AzureRateLimiter(
            max_rpm=config.azure_max_rpm,
            max_concurrent=config.azure_max_concurrent,
        )
    return _limiter_registry[key]


_STRIP_SCHEMA_KEYS = {"title", "default", "examples"}


def _prepare_schema(schema: dict) -> dict:
    """Prepare a Pydantic JSON schema for Azure OpenAI strict mode.

    - Inline ``$defs``/``$ref`` (Azure requires flat schemas)
    - Strip ``title``, ``default``, ``examples`` (unsupported in strict mode)
    - Add ``additionalProperties: false`` to every object
    - Ensure all properties listed in ``required``
    """
    defs = schema.pop("$defs", None) or {}

    def _resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                ref_name = node["$ref"].rsplit("/", 1)[-1]
                if ref_name in defs:
                    return _resolve(dict(defs[ref_name]))
                return node

            resolved = {}
            for k, v in node.items():
                if k in _STRIP_SCHEMA_KEYS:
                    continue
                resolved[k] = _resolve(v)

            if resolved.get("type") == "object":
                resolved.setdefault("additionalProperties", False)
                props = resolved.get("properties", {})
                if props:
                    resolved["required"] = list(props.keys())

            return resolved
        if isinstance(node, list):
            return [_resolve(item) for item in node]
        return node

    return _resolve(schema)


class AzureOpenAILLMAdapter:
    def __init__(self, config: LLMConfig):
        self._endpoint = config.azure_endpoint.rstrip("/")
        self._deployment = config.azure_deployment
        self._api_key = config.api_key
        self._limiter = _get_limiter(self._endpoint, self._deployment, config)
        self._client = httpx.AsyncClient(timeout=config.timeout if hasattr(config, "timeout") else 120.0)

    def _url(self) -> str:
        return (
            f"{self._endpoint}/openai/deployments/{self._deployment}"
            f"/chat/completions?api-version={_API_VERSION}"
        )

    def _headers(self) -> dict[str, str]:
        return {"api-key": self._api_key}

    async def _post_with_retry(self, **kwargs) -> httpx.Response:
        """POST with exponential backoff on 429. Acquires rate limiter before request."""
        await self._limiter.acquire()
        try:
            for attempt in range(_MAX_RETRIES):
                response = await self._client.post(**kwargs)

                if response.status_code != 429:
                    return response

                retry_after_ms = response.headers.get("retry-after-ms")
                if retry_after_ms:
                    delay = int(retry_after_ms) / 1000.0
                else:
                    retry_after_s = response.headers.get("retry-after")
                    if retry_after_s:
                        delay = float(retry_after_s)
                    else:
                        delay = min(_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1), _MAX_DELAY)

                self._limiter.report_429(delay)

                logger.info(
                    "Rate limited (429), retry %d/%d after %.1fs", attempt + 1, _MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)
                self._limiter.record_request()

            return response  # type: ignore[possibly-undefined]
        finally:
            self._limiter.release()

    async def generate(self, prompt: str, *, system: str | None = None) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = await self._post_with_retry(
            url=self._url(),
            json={"messages": messages, "temperature": 0.1},
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    async def generate_structured(
        self, prompt: str, schema: type[BaseModel], *, system: str | None = None
    ) -> BaseModel:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        json_schema = schema.model_json_schema()
        json_schema = _prepare_schema(json_schema)

        body: dict = {
            "messages": messages,
            "temperature": 0.1,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": json_schema,
                },
            },
        }

        try:
            response = await self._post_with_retry(
                url=self._url(), json=body, headers=self._headers(),
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            data = json.loads(raw)
            return schema.model_validate(data)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise  # propagate rate-limit errors, don't double-request via fallback
            body_text = exc.response.text[:500] if exc.response else ""
            logger.warning(
                "Structured output failed (HTTP %s): %s — falling back to prompt-based",
                exc.response.status_code, body_text,
            )
            return await self._generate_structured_fallback(prompt, schema, system=system)
        except json.JSONDecodeError as exc:
            logger.warning(
                "Structured output JSON parse failed (%s), falling back to prompt-based", exc,
            )
            return await self._generate_structured_fallback(prompt, schema, system=system)

    async def _generate_structured_fallback(
        self, prompt: str, schema: type[BaseModel], *, system: str | None = None
    ) -> BaseModel:
        """Fallback: append JSON schema to the prompt text."""
        schema_json = schema.model_json_schema()
        full_prompt = (
            f"{prompt}\n\n"
            f"Respond with valid JSON matching this schema:\n"
            f"```json\n{json.dumps(schema_json, indent=2)}\n```"
        )

        raw = await self.generate(full_prompt, system=system)
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            raw = "\n".join(lines)

        data = json.loads(raw)
        return schema.model_validate(data)

    async def close(self):
        await self._client.aclose()
