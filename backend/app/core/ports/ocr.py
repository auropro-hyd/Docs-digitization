"""OCR Engine port definition.

All OCR adapters (Marker, Azure DI, PaddleOCR, etc.) must implement this protocol.
The core domain depends only on this interface, never on concrete implementations.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field


class BoundingRegion(BaseModel):
    page_num: int
    x: float
    y: float
    width: float
    height: float


class OCRWord(BaseModel):
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    is_handwritten: bool = False
    bounding_region: BoundingRegion | None = None


class BarcodeResult(BaseModel):
    barcode_type: str
    value: str
    page_num: int
    bounding_region: BoundingRegion | None = None


class SelectionMark(BaseModel):
    state: str  # "selected" | "unselected"
    confidence: float = Field(ge=0.0, le=1.0)
    page_num: int
    bounding_region: BoundingRegion | None = None


class OCRPageResult(BaseModel):
    page_num: int
    markdown: str = ""
    words: list[OCRWord] = Field(default_factory=list)
    barcodes: list[BarcodeResult] = Field(default_factory=list)
    selection_marks: list[SelectionMark] = Field(default_factory=list)
    images: dict[str, bytes] = Field(default_factory=dict)


class OCRResult(BaseModel):
    pages: list[OCRPageResult] = Field(default_factory=list)
    full_markdown: str = ""
    raw_response: dict | None = Field(None, exclude=True)

    @property
    def total_pages(self) -> int:
        return len(self.pages)

    @property
    def all_words(self) -> list[OCRWord]:
        return [w for p in self.pages for w in p.words]


class OCREngine(Protocol):
    """Port for OCR extraction engines."""

    async def extract(self, pdf_path: str, pages: list[int] | None = None) -> OCRResult:
        """Extract text and structure from a PDF document."""
        ...

    def supports_handwriting(self) -> bool:
        """Whether this engine can detect handwritten content."""
        ...

    def supports_barcodes(self) -> bool:
        """Whether this engine can read barcodes/QR codes."""
        ...

    def supports_selection_marks(self) -> bool:
        """Whether this engine can detect checkboxes/radio buttons."""
        ...
