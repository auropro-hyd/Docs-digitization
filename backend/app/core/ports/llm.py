"""LLM Provider port definition.

All LLM adapters (Ollama, Azure OpenAI, etc.) must implement this protocol.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class LLMProvider(Protocol):
    """Port for LLM inference providers."""

    async def generate(self, prompt: str, *, system: str | None = None) -> str:
        """Generate a text response from a prompt."""
        ...

    async def generate_structured(
        self, prompt: str, schema: type[BaseModel], *, system: str | None = None
    ) -> BaseModel:
        """Generate a structured response conforming to a Pydantic schema."""
        ...
