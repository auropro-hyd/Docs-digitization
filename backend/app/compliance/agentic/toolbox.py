"""Context toolbox for agentic audit workers."""

from __future__ import annotations

import logging
import os
import sys

from app.compliance.summarizer import load_summary

logger = logging.getLogger(__name__)

_DEBUG = os.getenv("COMPLIANCE_AGENTIC_DEBUG") == "1"


def _dbg(msg: str) -> None:
    if _DEBUG:
        print(f"[toolbox] {msg}", file=sys.stderr, flush=True)


class ContextToolbox:
    def __init__(
        self,
        all_extractions: list[dict],
        section_map: dict[int, dict],
        doc_id: str,
        page_cap: int = 50,
    ) -> None:
        self._all_extractions = all_extractions
        self._section_map = section_map
        self._doc_id = doc_id
        self._page_cap = page_cap
        self._summary_cache: dict[tuple[str, str | None], str] = {}

    def get_context_summary(
        self,
        document_type: str,
        section_type: str | None = None,
    ) -> str:
        key = (document_type, section_type)
        if key not in self._summary_cache:
            result = load_summary(self._doc_id, document_type, section_type) or ""
            _dbg(
                f"get_context_summary({document_type!r}, {section_type!r}) "
                f"→ {len(result)} chars"
                + (f": {result[:120]!r}..." if result else ": [empty]")
            )
            self._summary_cache[key] = result
        return self._summary_cache[key]

    def get_context_pages(
        self,
        document_type: str,
        section_type: str | None = None,
        page_nums: list[int] | None = None,
    ) -> str:
        all_matching = self._get_matching_extractions(document_type, section_type)
        available_page_nums = [p.get("page_num") for p in all_matching]

        if page_nums is not None:
            matching = [p for p in all_matching if p.get("page_num") in page_nums]
        else:
            matching = all_matching

        # Track truncation so we can surface it to the LLM rather than
        # silently dropping tail pages — a 35-page batch_record's
        # manufacturing_operations section would otherwise lose its
        # back third with no signal. Without this marker the LLM has
        # no way to know its context window is incomplete.
        total_pre_cap = len(matching)
        truncated_count = max(0, total_pre_cap - self._page_cap)
        matching = matching[: self._page_cap]

        _dbg(
            f"get_context_pages({document_type!r}, {section_type!r}, page_nums={page_nums}) "
            f"→ {len(matching)} pages (cap={self._page_cap}, dropped={truncated_count}): "
            f"{[p.get('page_num') for p in matching]}"
        )

        if not matching and page_nums is not None and available_page_nums:
            hint = (
                f"[No pages found for {document_type!r} at page_nums={page_nums}. "
                f"Available pages for this document type: {available_page_nums}. "
                f"Call get_context_pages without page_nums to retrieve all pages, "
                f"or specify one of the available page numbers above.]"
            )
            _dbg(f"returning hint: {hint}")
            return hint

        parts: list[str] = []
        for ext in matching:
            pn = ext.get("page_num", "?")
            md = str(ext.get("markdown", "") or "")
            label = f"[{document_type}/{section_type or 'all'}/p{pn}]"
            parts.append(f"{label}\n{md}")

        if truncated_count > 0:
            dropped_pages = [
                p.get("page_num") for p in all_matching[self._page_cap:]
            ]
            parts.append(
                f"[truncated: {truncated_count} additional page(s) omitted "
                f"to stay within the {self._page_cap}-page context cap; "
                f"dropped page_num values: {dropped_pages}. If a verdict "
                f"requires evidence from those pages, call "
                f"get_context_pages again with explicit page_nums.]"
            )
        return "\n\n".join(parts)

    def _get_matching_extractions(
        self, document_type: str, section_type: str | None
    ) -> list[dict]:
        result: list[dict] = []
        for ext in self._all_extractions:
            p = ext.get("page_num", 0)
            meta = self._section_map.get(p, {})
            if meta.get("document_type") != document_type:
                continue
            if section_type is not None and meta.get("section_type") != section_type:
                continue
            result.append(ext)
        return result
