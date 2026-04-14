"""Vision Language Model provider port definition.

All VLM adapters (Gemini, vLLM-hosted Qwen/InternVL, etc.) must implement
this protocol.  The core domain depends only on this interface.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class VLMProvider(Protocol):
    """Port for Vision-Language Model inference."""

    async def analyze_image(
        self,
        image: bytes,
        prompt: str,
        *,
        system: str | None = None,
        mime_type: str = "image/png",
    ) -> str:
        """Analyze an image with a text prompt, returning free-text response."""
        ...

    async def analyze_image_structured(
        self,
        image: bytes,
        prompt: str,
        schema: type[BaseModel],
        *,
        system: str | None = None,
        mime_type: str = "image/png",
    ) -> BaseModel:
        """Analyze an image with structured output conforming to a Pydantic schema."""
        ...

    def supports_structured_output(self) -> bool:
        """Whether this provider natively supports JSON schema / structured output."""
        ...

    def max_image_resolution(self) -> tuple[int, int]:
        """Maximum image resolution (width, height) this provider accepts."""
        ...
