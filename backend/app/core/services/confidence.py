"""Composite confidence scoring service.

Combines multiple signal sources (Docling quality metrics, Azure DI per-word
confidence, Marker LLM table scores, and custom validation rules) into a
single composite confidence score per page.

No single tool provides complete confidence, so we build it from all sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.models.quality import PageQualityScore


@dataclass
class ValidationResults:
    """Results from custom plausibility checks."""

    rules_checked: int = 0
    rules_passed: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if self.rules_checked == 0:
            return 1.0
        return self.rules_passed / self.rules_checked


@dataclass
class CompositeConfidenceWeights:
    docling_mean: float = 0.30
    azure_di_min_word: float = 0.25
    marker_table: float = 0.15
    validation: float = 0.30


class CompositeConfidenceScorer:
    def __init__(self, weights: CompositeConfidenceWeights | None = None):
        self._weights = weights or CompositeConfidenceWeights()

    def score_page(
        self,
        docling_page: PageQualityScore | None = None,
        azure_di_word_confidences: list[float] | None = None,
        marker_table_score: int | None = None,
        validation_results: ValidationResults | None = None,
    ) -> float:
        w = self._weights

        docling_mean = docling_page.mean_score if docling_page else 0.5
        azure_min = min(azure_di_word_confidences) if azure_di_word_confidences else 0.5
        table_norm = (marker_table_score / 5.0) if marker_table_score else 0.5
        val_rate = validation_results.pass_rate if validation_results else 0.8

        score = (
            w.docling_mean * docling_mean
            + w.azure_di_min_word * azure_min
            + w.marker_table * table_norm
            + w.validation * val_rate
        )

        return round(min(max(score, 0.0), 1.0), 4)

    def classify_confidence(self, score: float) -> str:
        """Classify a score into confidence tiers for HITL routing."""
        if score >= 0.9:
            return "high"
        if score >= 0.7:
            return "medium"
        return "low"
