"""Docling quality scoring adapter.

Uses IBM's Docling (MIT license, free) to produce per-page quality metrics.
Runs CPU-only. Used solely for quality assessment, not for extraction content.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.models.quality import PageQualityScore, QualityReport

logger = logging.getLogger(__name__)


class DoclingQualityAdapter:
    def __init__(self):
        self._converter = None

    def _get_converter(self):
        if self._converter is not None:
            return self._converter

        from docling.document_converter import DocumentConverter

        self._converter = DocumentConverter()
        return self._converter

    async def score(self, pdf_path: str) -> QualityReport:
        converter = self._get_converter()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, converter.convert, pdf_path)

        confidence = getattr(result, "confidence", None)
        if confidence is None:
            logger.warning("Docling did not produce a confidence report for %s", pdf_path)
            return QualityReport(
                layout_score=0.5,
                table_score=0.5,
                ocr_score=0.5,
                parse_score=0.5,
                mean_score=0.5,
            )

        per_page: dict[int, PageQualityScore] = {}
        pages_data = getattr(confidence, "pages", {}) or {}
        for page_num, page_conf in pages_data.items():
            per_page[int(page_num)] = PageQualityScore(
                page_num=int(page_num),
                layout_score=getattr(page_conf, "layout_score", 0.5),
                table_score=getattr(page_conf, "table_score", 0.5),
                ocr_score=getattr(page_conf, "ocr_score", 0.5),
                parse_score=getattr(page_conf, "parse_score", 0.5),
            )

        return QualityReport(
            layout_score=getattr(confidence, "layout_score", 0.5),
            table_score=getattr(confidence, "table_score", 0.5),
            ocr_score=getattr(confidence, "ocr_score", 0.5),
            parse_score=getattr(confidence, "parse_score", 0.5),
            mean_score=getattr(confidence, "mean_score", 0.5),
            low_score=getattr(confidence, "low_score", None),
            per_page=per_page,
        )
