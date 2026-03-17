"""FastAPI dependency injection wiring.

Uses FastAPI's Depends() to inject port implementations into route handlers.
"""

from __future__ import annotations

from app.config.container import Container, get_container
from app.core.ports.llm import LLMProvider
from app.core.ports.ocr import OCREngine
from app.core.ports.quality import QualityScorer
from app.core.ports.storage import DocumentStore


def get_ocr_engine() -> OCREngine:
    """Get the OCR engine for the active pipeline mode."""
    return get_container().ocr_engine


def get_quality_scorer() -> QualityScorer:
    """Get quality scorer (only meaningful in marker_docling mode)."""
    return get_container().quality_scorer


def get_llm() -> LLMProvider:
    return get_container().llm


def get_document_store() -> DocumentStore:
    return get_container().document_store


def get_di_container() -> Container:
    return get_container()
