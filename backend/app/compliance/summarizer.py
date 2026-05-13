"""Page summary generation and storage for the compliance pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.config.settings import get_settings
from app.core.ports.llm import LLMProvider

logger = logging.getLogger(__name__)

_PAGE_SUMMARY_SYSTEM = (
    "Extract and summarize the key data from this pharmaceutical document page in 3-5 sentences.\n"
    "Focus ONLY on the factual content — do NOT describe which form or section the page belongs to, "
    "that context is already known. Instead list:\n"
    "- Materials/items present: name, item code, and quantities (standard, requested, issued, "
    "dispensed, allocated)\n"
    "- Batch/lot numbers, AR numbers, container counts, and expiry/retest dates\n"
    "- Process parameters recorded: temperatures, vacuum levels, pH values, durations\n"
    "- Operator and checker names, initials, or signatures with dates\n"
    "- Any reference numbers: deviation, change control, specification, or AR numbers\n"
    "Preserve exact numeric values. Omit section headers and form titles — only the data matters.\n"
    "This summary is consumed by a cross-document compliance audit agent."
)


def _summaries_file(doc_id: str) -> Path:
    return Path(get_settings().storage.base_path) / doc_id / "summaries" / "page_summaries.json"


def load_summary(doc_id: str, document_type: str, section_type: str | None) -> str | None:
    """Load page summaries for a (doc_type, section_type) pair from the package summary file.

    Returns texts joined in page-number order, or None if no matching entries exist.
    """
    path = _summaries_file(doc_id)
    try:
        data: dict = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    matching: list[tuple[int, str]] = []
    for page_key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("doc_type") != document_type:
            continue
        if section_type is not None and entry.get("section_type") != section_type:
            continue
        try:
            page_num = int(page_key)
        except (ValueError, TypeError):
            continue
        text = entry.get("text", "")
        if text:
            matching.append((page_num, text))

    if not matching:
        return None
    matching.sort(key=lambda x: x[0])
    return "\n\n".join(text for _, text in matching)


def store_page_summary(
    doc_id: str,
    page_num: int,
    document_type: str,
    section_type: str | None,
    text: str,
) -> None:
    """Merge a single page summary entry into page_summaries.json (read-merge-write)."""
    path = _summaries_file(doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data: dict = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}

    data[str(page_num)] = {
        "text": text,
        "doc_type": document_type,
        "section_type": section_type,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def summarize_pages_in_batches(
    extractions: list[dict],
    section_map: dict[int, dict],
    doc_id: str,
    llm: LLMProvider,
    batch_size: int = 10,
) -> None:
    """Generate page summaries for all pages not already in page_summaries.json.

    Skips pages already present. Dispatches batches of `batch_size` in parallel.
    """
    path = _summaries_file(doc_id)
    try:
        existing: dict = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        existing = {}

    pending = [ext for ext in extractions if str(ext.get("page_num", "")) not in existing]
    if not pending:
        return

    async def _summarize_one(ext: dict) -> None:
        page_num = ext.get("page_num", 0)
        markdown = str(ext.get("markdown", "") or "")
        if not markdown.strip():
            return
        meta = section_map.get(page_num, {})
        doc_type = meta.get("document_type") or ext.get("document_type", "")
        sec_type = meta.get("section_type") or ext.get("section_type")
        try:
            text = await llm.generate(markdown, system=_PAGE_SUMMARY_SYSTEM)
            if text:
                store_page_summary(doc_id, page_num, doc_type, sec_type, text)
        except Exception:
            logger.warning("Page summary generation failed for page %s", page_num, exc_info=True)

    for i in range(0, len(pending), batch_size):
        batch = pending[i : i + batch_size]
        await asyncio.gather(*[_summarize_one(ext) for ext in batch])
