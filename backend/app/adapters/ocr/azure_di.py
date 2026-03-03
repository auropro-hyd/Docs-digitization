"""Azure Document Intelligence OCR adapter.

Uses Azure AI Foundry cloud API for dev/staging and disconnected container
for on-prem production. Same adapter class, different endpoint in config.

Provides: per-word handwriting detection, per-field confidence scores,
barcode reading (17+ types), selection mark detection, cross-page table support.
"""

from __future__ import annotations

import asyncio
import logging

from app.config.settings import AzureDIConfig
from app.core.ports.ocr import (
    BarcodeResult,
    BoundingRegion,
    OCRPageResult,
    OCRResult,
    OCRWord,
    SelectionMark,
)

logger = logging.getLogger(__name__)


def _to_bounding_region(regions: list | None, page_num: int) -> BoundingRegion | None:
    if not regions:
        return None
    r = regions[0]
    polygon = getattr(r, "polygon", None) or []
    if len(polygon) >= 4:
        x_coords = [polygon[i] for i in range(0, len(polygon), 2)]
        y_coords = [polygon[i] for i in range(1, len(polygon), 2)]
        return BoundingRegion(
            page_num=page_num,
            x=min(x_coords),
            y=min(y_coords),
            width=max(x_coords) - min(x_coords),
            height=max(y_coords) - min(y_coords),
        )
    return None


class AzureDIOCRAdapter:
    def __init__(self, config: AzureDIConfig):
        self._config = config
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client

        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential

        self._client = DocumentIntelligenceClient(
            endpoint=self._config.endpoint,
            credential=AzureKeyCredential(self._config.api_key),
        )
        return self._client

    async def extract(self, pdf_path: str, pages: list[int] | None = None) -> OCRResult:
        client = self._get_client()
        loop = asyncio.get_event_loop()

        def _analyze():
            from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()

            poller = client.begin_analyze_document(
                "prebuilt-layout",
                analyze_request=AnalyzeDocumentRequest(bytes_source=pdf_bytes),
                features=self._config.features,
            )
            return poller.result()

        result = await loop.run_in_executor(None, _analyze)

        ocr_pages: list[OCRPageResult] = []
        content = result.content or ""

        for az_page in result.pages or []:
            page_num = az_page.page_number
            if pages and page_num not in pages:
                continue

            words: list[OCRWord] = []
            for word in az_page.words or []:
                words.append(
                    OCRWord(
                        text=word.content,
                        confidence=getattr(word, "confidence", 0.0),
                        is_handwritten=getattr(word, "is_handwritten", False) or False,
                        bounding_region=_to_bounding_region(getattr(word, "bounding_regions", None), page_num),
                    )
                )

            barcodes: list[BarcodeResult] = []
            for bc in getattr(az_page, "barcodes", None) or []:
                barcodes.append(
                    BarcodeResult(
                        barcode_type=getattr(bc, "kind", "unknown"),
                        value=getattr(bc, "value", ""),
                        page_num=page_num,
                    )
                )

            selection_marks: list[SelectionMark] = []
            for sm in az_page.selection_marks or []:
                selection_marks.append(
                    SelectionMark(
                        state=sm.state or "unselected",
                        confidence=getattr(sm, "confidence", 0.0),
                        page_num=page_num,
                    )
                )

            page_text = " ".join(w.text for w in words)

            ocr_pages.append(
                OCRPageResult(
                    page_num=page_num,
                    markdown=page_text,
                    words=words,
                    barcodes=barcodes,
                    selection_marks=selection_marks,
                )
            )

        return OCRResult(
            pages=ocr_pages,
            full_markdown=content,
        )

    def supports_handwriting(self) -> bool:
        return True

    def supports_barcodes(self) -> bool:
        return True

    def supports_selection_marks(self) -> bool:
        return True
