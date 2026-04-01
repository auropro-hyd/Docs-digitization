"""OCR Engine port definition.

All OCR adapters (Marker, Azure DI, PaddleOCR, etc.) must implement this protocol.
The core domain depends only on this interface, never on concrete implementations.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, Field

ProgressCallback = Callable[[int, str], None]


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


class FormulaResult(BaseModel):
    kind: str = "inline"  # "inline" | "display"
    value: str = ""  # LaTeX expression
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    page_num: int = 0


class KeyValuePair(BaseModel):
    key: str
    value: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    page_num: int = 0
    bounding_region: BoundingRegion | None = None


class StyleSpan(BaseModel):
    is_handwritten: bool = False
    font_family: str = ""
    font_style: str = "normal"  # "normal" | "italic"
    font_weight: str = "normal"  # "normal" | "bold"
    color: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    offset: int = 0
    length: int = 0


class SignatureRegion(BaseModel):
    """Detected signature region on a page."""
    page_num: int
    status: str = "unsigned"  # "signed" | "unsigned"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    bounding_region: BoundingRegion | None = None
    label: str = ""


class LanguageSpan(BaseModel):
    locale: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    offset: int = 0
    length: int = 0


class OCRPageResult(BaseModel):
    page_num: int
    markdown: str = ""
    page_width: float | None = None
    page_height: float | None = None
    page_unit: str | None = None
    words: list[OCRWord] = Field(default_factory=list)
    barcodes: list[BarcodeResult] = Field(default_factory=list)
    selection_marks: list[SelectionMark] = Field(default_factory=list)
    formulas: list[FormulaResult] = Field(default_factory=list)
    images: dict[str, bytes] = Field(default_factory=dict)


class OCRResult(BaseModel):
    pages: list[OCRPageResult] = Field(default_factory=list)
    full_markdown: str = ""
    table_metadata: list[dict] = Field(default_factory=list)
    key_value_pairs: list[KeyValuePair] = Field(default_factory=list)
    styles: list[StyleSpan] = Field(default_factory=list)
    signatures: list[SignatureRegion] = Field(default_factory=list)
    languages: list[LanguageSpan] = Field(default_factory=list)
    raw_response: dict | None = Field(None, exclude=True)

    @property
    def total_pages(self) -> int:
        return len(self.pages)

    @property
    def all_words(self) -> list[OCRWord]:
        return [w for p in self.pages for w in p.words]


class OCREngine(Protocol):
    """Port for OCR extraction engines."""

    async def extract(
        self,
        pdf_path: str,
        pages: list[int] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> OCRResult:
        """Extract text and structure from a PDF document.

        Args:
            progress_callback: Optional ``(percent: int, status: str) -> None``
                called periodically during long-running analysis.  ``percent``
                is 0-100, ``status`` is a human-readable label.
        """
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
