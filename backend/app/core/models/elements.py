"""Domain element models for extracted document content.

These models represent the atomic units of extracted content (tables, signatures,
key-value pairs, etc.) and are engine-agnostic -- no imports from Marker, Azure DI,
or any other external library.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ElementType(StrEnum):
    TABLE = "table"
    KEY_VALUE = "key_value"
    TEXT_BLOCK = "text_block"
    SIGNATURE = "signature"
    CHECKBOX = "checkbox"
    IMAGE = "image"


class DocumentElement(BaseModel):
    """Base element extracted from a document page."""

    element_type: ElementType
    content: Any
    page_num: int
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    source_span: tuple[int, int] | None = None


class MergedCell(BaseModel):
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1


class TableElement(BaseModel):
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    merged_cells: list[MergedCell] = Field(default_factory=list)
    is_cross_page: bool = False
    marker_table_score: int | None = Field(None, ge=1, le=5)


class KeyValueElement(BaseModel):
    key: str
    value: str
    is_handwritten: bool | None = None


class SignatureElement(BaseModel):
    field_name: str
    is_signed: bool | None = None
    signer_name: str | None = None
    date: str | None = None
    is_handwritten: bool | None = None


class CheckboxElement(BaseModel):
    label: str
    is_checked: bool | None = None


class TextBlockElement(BaseModel):
    text: str
    is_handwritten: bool = False


class ImageElement(BaseModel):
    image_path: str
    description: str | None = None
    alt_text: str | None = None
