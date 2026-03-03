"""Quality Scorer port definition.

Quality scoring adapters (Docling, etc.) must implement this protocol.
Provides page-level and document-level quality metrics.
"""

from __future__ import annotations

from typing import Protocol

from app.core.models.quality import QualityReport


class QualityScorer(Protocol):
    """Port for document quality scoring engines."""

    async def score(self, pdf_path: str) -> QualityReport:
        """Produce a quality report for the given PDF."""
        ...
