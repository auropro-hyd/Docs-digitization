"""Document segmentation service.

Identifies distinct sub-documents within a multi-part document packet using
a single LLM call.  Section types are free-form (LLM-generated), not enums.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.compliance.models import DocumentSection, DocumentSegmentation
from app.compliance.rules.profiles import (
    load_profiles,
    normalize_document_type,
    normalize_section_type,
)
from app.core.ports.llm import LLMProvider

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a document structure analyst for pharmaceutical regulatory documents. "
    "You identify distinct sub-documents within a scanned document packet. "
    "You MUST respond with valid JSON matching the provided schema."
)

_CHARS_PER_PAGE = 500


def _build_segmentation_prompt(
    extractions: list[dict],
    filename: str = "",
) -> str:
    page_summaries = []
    for ext in extractions:
        page_num = ext.get("page_num", 0)
        md = ext.get("markdown", "")
        page_summaries.append(f"Page {page_num}: {md[:_CHARS_PER_PAGE]}")

    profiles = load_profiles()
    allowed_doc_types = ", ".join(sorted(profiles.document_profiles.keys()))

    section_type_hints = []
    for doc_type_key in sorted(profiles.document_profiles.keys()):
        profile = profiles.document_profiles[doc_type_key]
        if profile.expected_sections:
            types = ", ".join(s.section_type for s in profile.expected_sections)
            section_type_hints.append(f"  {doc_type_key}: {types}")
        else:
            section_type_hints.append(f"  {doc_type_key}: (use a descriptive lowercase_snake_case name)")
    section_type_guide = "\n".join(section_type_hints)

    return (
        f"Analyze this multi-part document and identify each distinct sub-document/section.\n\n"
        f"Look for: page numbering restarts, document titles, headers that change, "
        f"form layout shifts, and content topic changes.\n\n"
        f"FILENAME: {filename}\n\n"
        f"PAGE SUMMARIES:\n" + "\n\n".join(page_summaries) + "\n\n"
        f"For each section return:\n"
        f"- section_id: short lowercase_snake_case slug\n"
        f"- name: descriptive human-readable name\n"
        f"- document_type: one of: {allowed_doc_types}\n"
        f"  If this section is a sub-section of a larger document already classified above, "
        f"repeat that document's type.\n"
        f"- section_type: choose from the known types for the document_type, or use a descriptive "
        f"lowercase_snake_case name if none fit:\n{section_type_guide}\n"
        f"- start_page / end_page: inclusive page range\n"
        f"- description: brief description of the section content\n\n"
        f"Also return the overall document_type and your confidence (0.0-1.0)."
    )


class DocumentSegmenter:
    """Identifies sub-documents within a document packet via LLM."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def segment(
        self,
        extractions: list[dict],
        key_value_pairs: list[dict] | None = None,
        filename: str = "",
        total_pages: int = 0,
    ) -> DocumentSegmentation:
        prompt = _build_segmentation_prompt(extractions, filename)
        try:
            result = await self._llm.generate_structured(
                prompt, DocumentSegmentation, system=_SYSTEM,
            )
            if not isinstance(result, DocumentSegmentation):
                result = DocumentSegmentation.model_validate(result)
            return result
        except Exception:
            logger.exception("Segmentation failed, returning single-section fallback")
            return DocumentSegmentation(
                sections=[DocumentSection(
                    section_id="full_document",
                    name=filename or "Full Document",
                    section_type="unknown",
                    start_page=1,
                    end_page=total_pages or len(extractions),
                )],
                document_type="unknown",
                confidence=0.0,
            )


def load_segmentation(doc_dir: Path) -> DocumentSegmentation | None:
    """Load cached segmentation from disk."""
    path = doc_dir / "segmentation.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return DocumentSegmentation.model_validate(data)
    except Exception:
        logger.warning("Failed to load segmentation from %s", path)
        return None


def store_segmentation(doc_dir: Path, seg: DocumentSegmentation) -> None:
    """Persist segmentation to disk."""
    doc_dir.mkdir(parents=True, exist_ok=True)
    path = doc_dir / "segmentation.json"
    path.write_text(seg.model_dump_json(indent=2), encoding="utf-8")


def build_page_to_section(seg: DocumentSegmentation) -> dict[int, dict]:
    """Build a lookup from page number to section info dict."""
    page_map: dict[int, dict] = {}
    for sec in seg.sections:
        info = {
            "section_id": sec.section_id,
            "section_name": sec.name,
            "section_type": normalize_section_type(sec.section_type),
            "document_type": normalize_document_type(sec.document_type) if sec.document_type else "",
            "start_page": sec.start_page,
            "end_page": sec.end_page,
        }
        for p in range(sec.start_page, sec.end_page + 1):
            page_map[p] = info
    return page_map
