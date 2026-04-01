"""Gold label schema for extraction benchmark fixtures."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class GoldFieldLabel(BaseModel):
    field_id: str
    raw_value: str = ""
    normalized_value: str = ""
    expected_page: int | None = None
    expected_region: list[float] | None = None  # [x1, y1, x2, y2], normalized 0..1
    criticality: str = "major"  # critical | major | minor | observation
    placeholder_allowed: bool = False
    handwriting_expected: bool = False
    notes: str = ""

    @model_validator(mode="after")
    def _validate_region(self):
        if self.expected_region is None:
            return self
        if len(self.expected_region) != 4:
            raise ValueError("expected_region must contain exactly 4 coordinates")
        x1, y1, x2, y2 = self.expected_region
        if not (0.0 <= x1 <= 1.0 and 0.0 <= y1 <= 1.0 and 0.0 <= x2 <= 1.0 and 0.0 <= y2 <= 1.0):
            raise ValueError("expected_region coordinates must be normalized between 0 and 1")
        if x1 > x2 or y1 > y2:
            raise ValueError("expected_region must be ordered as [x1, y1, x2, y2]")
        return self


class GoldLabelDocument(BaseModel):
    sample_id: str
    document_type: str
    fields: list[GoldFieldLabel] = Field(default_factory=list)
