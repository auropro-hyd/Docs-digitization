"""LangExtract adapter for compliance field extraction with source grounding.

Used selectively (NOT for every page) for compliance-critical fields like
signatures, dates, and batch numbers. Source grounding (char_interval) maps
each extraction to exact character positions in the source markdown.

Note: LangExtract's ScoredOutput.score is hardcoded to 1.0 for all providers.
We use alignment_status (MATCH_EXACT/MATCH_FUZZY/None) as a proxy signal instead.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from app.config.settings import LLMConfig

logger = logging.getLogger(__name__)


@dataclass
class ExtractionField:
    """A single extracted field with source grounding."""

    key: str
    value: str
    char_start: int | None = None
    char_end: int | None = None
    alignment_status: str | None = None  # MATCH_EXACT, MATCH_FUZZY, or None


@dataclass
class LangExtractResult:
    fields: list[ExtractionField] = field(default_factory=list)
    source_text: str = ""


class LangExtractAdapter:
    def __init__(self, config: LLMConfig):
        self._model_id = config.model
        self._model_url = config.base_url

    async def extract_compliance_fields(
        self,
        page_markdown: str,
        description: str = "Extract all signature attestations, dates, and batch numbers",
        examples: list | None = None,
    ) -> LangExtractResult:
        """Extract compliance-critical fields with source grounding."""
        loop = asyncio.get_event_loop()

        def _do_extract():
            import langextract as lx

            kwargs = {
                "text_or_documents": page_markdown,
                "prompt_description": description,
                "model_id": self._model_id,
                "model_url": self._model_url,
                "fence_output": False,
                "use_schema_constraints": False,
            }
            if examples:
                kwargs["examples"] = examples

            return lx.extract(**kwargs)

        try:
            result = await loop.run_in_executor(None, _do_extract)
        except Exception:
            logger.exception("LangExtract extraction failed")
            return LangExtractResult(source_text=page_markdown)

        fields: list[ExtractionField] = []
        for ext in getattr(result, "extractions", []):
            char_interval = getattr(ext, "char_interval", None)
            char_start = char_interval[0] if char_interval and len(char_interval) > 0 else None
            char_end = char_interval[1] if char_interval and len(char_interval) > 1 else None

            alignment = getattr(ext, "alignment_status", None)
            alignment_str = str(alignment) if alignment else None

            extracted_data = getattr(ext, "data", {})
            if isinstance(extracted_data, dict):
                for key, value in extracted_data.items():
                    fields.append(
                        ExtractionField(
                            key=str(key),
                            value=str(value),
                            char_start=char_start,
                            char_end=char_end,
                            alignment_status=alignment_str,
                        )
                    )
            else:
                fields.append(
                    ExtractionField(
                        key="extraction",
                        value=str(extracted_data),
                        char_start=char_start,
                        char_end=char_end,
                        alignment_status=alignment_str,
                    )
                )

        return LangExtractResult(fields=fields, source_text=page_markdown)
