"""Quality scoring models.

These models represent quality assessment results from any scoring engine
(currently Docling). They are engine-agnostic.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class QualityGrade(StrEnum):
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    FAIR = "FAIR"
    POOR = "POOR"


class PageQualityScore(BaseModel):
    page_num: int
    layout_score: float = Field(ge=0.0, le=1.0)
    table_score: float = Field(ge=0.0, le=1.0)
    ocr_score: float = Field(ge=0.0, le=1.0)
    parse_score: float = Field(ge=0.0, le=1.0)

    @property
    def mean_score(self) -> float:
        return (self.layout_score + self.table_score + self.ocr_score + self.parse_score) / 4.0

    @property
    def grade(self) -> QualityGrade:
        s = self.mean_score
        if s >= 0.9:
            return QualityGrade.EXCELLENT
        if s >= 0.7:
            return QualityGrade.GOOD
        if s >= 0.5:
            return QualityGrade.FAIR
        return QualityGrade.POOR


class QualityReport(BaseModel):
    layout_score: float = Field(ge=0.0, le=1.0)
    table_score: float = Field(ge=0.0, le=1.0)
    ocr_score: float = Field(ge=0.0, le=1.0)
    parse_score: float = Field(ge=0.0, le=1.0)
    mean_score: float = Field(ge=0.0, le=1.0)
    low_score: float | None = None
    per_page: dict[int, PageQualityScore] = Field(default_factory=dict)
