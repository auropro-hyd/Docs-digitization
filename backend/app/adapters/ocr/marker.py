"""Marker OCR adapter.

Wraps Marker v1.10+ PdfConverter for PDF-to-Markdown conversion with LLM-powered
processors for cross-page table merging, handwriting OCR, form extraction, etc.
Uses OllamaService for on-prem LLM inference.
"""

from __future__ import annotations

import asyncio
import logging

from app.config.settings import MarkerConfig
from app.core.ports.ocr import (
    OCRPageResult,
    OCRResult,
)

logger = logging.getLogger(__name__)

PAGE_SEPARATOR = "\n\n---\n\n"


class MarkerOCRAdapter:
    def __init__(self, config: MarkerConfig):
        self._config = config
        self._converter = None

    def _get_converter(self):
        """Lazy-init Marker converter (heavy model loading)."""
        if self._converter is not None:
            return self._converter

        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict

        artifact_dict = create_model_dict()
        converter_config = {
            "use_llm": self._config.use_llm,
            "paginate_output": self._config.paginate_output,
            "extract_images": self._config.extract_images,
            "no_merge_tables_across_pages": self._config.no_merge_tables_across_pages,
            "table_height_threshold": self._config.table_height_threshold,
            "max_table_rows": self._config.max_table_rows,
            "html_tables_in_markdown": self._config.html_tables_in_markdown,
        }

        kwargs: dict = {
            "artifact_dict": artifact_dict,
            "config": converter_config,
        }

        if self._config.use_llm:
            kwargs["llm_service"] = "marker.services.ollama.OllamaService"

        self._converter = PdfConverter(**kwargs)
        return self._converter

    async def extract(self, pdf_path: str, pages: list[int] | None = None, progress_callback=None) -> OCRResult:
        # Marker's PdfConverter is a single blocking call with no
        # native progress hooks. Emit synthetic milestones so the
        # frontend doesn't sit at the same percent for the whole run:
        # 5% on submit, then a 1Hz heartbeat with elapsed time, then
        # 100% on completion. The percent never moves backwards;
        # the heartbeat label is the user's signal that the engine
        # is still working.
        if progress_callback:
            progress_callback(5, "Marker: loading models")

        converter = self._get_converter()
        loop = asyncio.get_event_loop()
        convert_task = loop.run_in_executor(None, converter, pdf_path)

        if progress_callback:
            progress_callback(10, "Marker: converting PDF")

        elapsed_s = 0.0
        while True:
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(convert_task), timeout=1.0,
                )
                break
            except asyncio.TimeoutError:
                elapsed_s += 1.0
                if progress_callback:
                    # Stay at 10% for the duration; the whole point of
                    # the heartbeat is the *label*, not the bar.
                    progress_callback(10, f"Marker: converting PDF ({elapsed_s:.0f}s)")

        rendered = result.markdown
        all_images: dict[str, bytes] = getattr(result, "images", {}) or {}

        page_markdowns = rendered.split(PAGE_SEPARATOR) if PAGE_SEPARATOR in rendered else [rendered]

        ocr_pages: list[OCRPageResult] = []
        for i, page_md in enumerate(page_markdowns):
            page_num = i + 1
            if pages and page_num not in pages:
                continue

            # Collect images referenced in this page's markdown
            page_images: dict[str, bytes] = {}
            for img_name, img_data in all_images.items():
                if img_name in page_md:
                    page_images[img_name] = img_data

            ocr_pages.append(
                OCRPageResult(
                    page_num=page_num,
                    markdown=page_md.strip(),
                    words=[],
                    images=page_images,
                )
            )

        if progress_callback:
            progress_callback(100, "Marker: extraction complete")

        return OCRResult(
            pages=ocr_pages,
            full_markdown=rendered,
        )

    def supports_handwriting(self) -> bool:
        return self._config.use_llm

    def supports_barcodes(self) -> bool:
        return False

    def supports_selection_marks(self) -> bool:
        return False
