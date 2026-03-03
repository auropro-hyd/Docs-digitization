"""Ollama LLM adapter for on-prem inference.

Connects to a local or remote Ollama instance for text generation
and structured output via Pydantic schema enforcement.
"""

from __future__ import annotations

import json
import logging

import httpx
from pydantic import BaseModel

from app.config.settings import LLMConfig

logger = logging.getLogger(__name__)


class OllamaLLMAdapter:
    def __init__(self, config: LLMConfig):
        self._base_url = config.base_url.rstrip("/")
        self._model = config.model
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=120.0)

    async def generate(self, prompt: str, *, system: str | None = None) -> str:
        payload: dict = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system

        response = await self._client.post("/api/generate", json=payload)
        response.raise_for_status()
        return response.json()["response"]

    async def generate_structured(
        self, prompt: str, schema: type[BaseModel], *, system: str | None = None
    ) -> BaseModel:
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
