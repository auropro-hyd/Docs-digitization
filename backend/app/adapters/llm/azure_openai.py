"""Azure OpenAI LLM adapter.

Used as a dev/staging fallback when Ollama is not available or for
higher-quality inference. Uses Azure AI Foundry deployments.
"""

from __future__ import annotations

import json
import logging

import httpx
from pydantic import BaseModel

from app.config.settings import LLMConfig

logger = logging.getLogger(__name__)


class AzureOpenAILLMAdapter:
    def __init__(self, config: LLMConfig):
        self._endpoint = config.azure_endpoint.rstrip("/")
        self._deployment = config.azure_deployment
        self._api_key = config.api_key
        self._client = httpx.AsyncClient(timeout=120.0)

    async def generate(self, prompt: str, *, system: str | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        url = f"{self._endpoint}/openai/deployments/{self._deployment}/chat/completions?api-version=2024-08-01-preview"
        response = await self._client.post(
            url,
            json={"messages": messages, "temperature": 0.1},
            headers={"api-key": self._api_key},
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

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
