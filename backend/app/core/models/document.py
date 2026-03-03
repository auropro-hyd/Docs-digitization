"""Domain models for the complete digitalized document.

Represents the full document structure tree:
DigitalDocument -> DocumentSection -> DocumentElement
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.models.elements import DocumentElement
from app.core.models.quality import QualityReport


class DocumentMetadata(BaseModel):
    filename: str
    total_pages: int
    document_type: str | None = None
    upload_date: datetime = Field(default_factory=datetime.utcnow)
    batch_number: str | None = None
    product_name: str | None = None


class DocumentSection(BaseModel):
    name: str
    section_type: str = "generic"
    page_range: tuple[int, int]
    elements: list[DocumentElement] = Field(default_factory=list)
    subsections: list[DocumentSection] = Field(default_factory=list)


class CrossReference(BaseModel):
    source_page: int
    target_page: int
    reference_type: str
    description: str | None = None


class DigitalDocument(BaseModel):
    doc_id: str
    metadata: DocumentMetadata
    sections: list[DocumentSection] = Field(default_factory=list)
    cross_references: list[CrossReference] = Field(default_factory=list)
    quality_report: QualityReport | None = None
    raw_markdown: dict[int, str] = Field(default_factory=dict)
