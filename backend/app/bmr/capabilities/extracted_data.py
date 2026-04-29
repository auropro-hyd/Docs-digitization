"""Pure in-memory representation of extracted BMR data.

The capabilities in Spec 003 consume these records; how they come into
being (OCR → field extraction) is the responsibility of Spec 001. For the
v0 slice, orchestrators and tests can construct them directly.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FieldValue(BaseModel):
    """A single extracted field with optional evidence region."""

    field: str
    value: Any
    entity_name: str | None = None  # e.g., material name for row-level fields
    page_bbox: tuple[float, float, float, float] | None = None  # (x1, y1, x2, y2)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source_doc_id: str | None = None
    source_page_index: int | None = None

    model_config = ConfigDict(frozen=True)


class ExtractedPage(BaseModel):
    """All fields extracted from a single page of a single document."""

    doc_id: str
    document_role: str
    page_index: int = Field(ge=1)
    tags: list[str] = Field(default_factory=list)
    fields: list[FieldValue] = Field(default_factory=list)
    # Per-page text content (markdown). Optional — populated when
    # available from the upstream OCR/extractor so downstream
    # capabilities can run text-aware analysis (Spec 007 BPCR section
    # detection, in particular) without re-reading raw OCR sidecars.
    # ``None`` means: not extracted, or the extractor source didn't
    # carry text content for this page.
    text: str | None = None
    # Spec 007 — populated only by the BPCR section tagger after Stage 3
    # extraction completes. ``None`` means: not a BPCR page, OR section
    # detection was disabled, OR the detector failed for this document.
    section_id: str | None = None
    # Spec 007 follow-up — reviewer-facing metadata stamped alongside
    # ``section_id`` so the report surface can show *why* a page was
    # tagged the way it was without re-running the detector. All
    # three are populated together when the tagger runs; when
    # ``section_id`` is ``None`` they are too.
    section_display_name: str | None = None
    section_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    section_detection_method: str | None = None

    model_config = ConfigDict(frozen=True)

    def get_fields(self, field_name: str) -> list[FieldValue]:
        return [f for f in self.fields if f.field == field_name]

    def find_single(self, field_name: str) -> FieldValue | None:
        matches = self.get_fields(field_name)
        if not matches:
            return None
        if len(matches) > 1:
            return None  # ambiguous; caller decides how to treat
        return matches[0]


class ExtractedPackage(BaseModel):
    """Container holding every ExtractedPage for a run."""

    package_id: str
    pages: list[ExtractedPage] = Field(default_factory=list)

    model_config = ConfigDict(frozen=True)

    def pages_by_role(self, role: str) -> list[ExtractedPage]:
        return [p for p in self.pages if p.document_role == role]


__all__ = ["ExtractedPackage", "ExtractedPage", "FieldValue"]
