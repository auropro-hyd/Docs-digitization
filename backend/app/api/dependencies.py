"""FastAPI dependency injection wiring.

Uses FastAPI's Depends() to inject port implementations into route handlers.
"""

from __future__ import annotations

from app.config.container import get_container
from app.core.ports.llm import LLMProvider
from app.core.ports.ocr import OCREngine
from app.core.ports.quality import QualityScorer
from app.core.ports.storage import DocumentStore


def get_primary_ocr() -> OCREngine:
    return get_container().primary_ocr


def get_secondary_ocr() -> OCREngine:
    return get_container().secondary_ocr


def get_quality_scorer() -> QualityScorer:
    return get_container().quality_scorer


def get_llm() -> LLMProvider:
    return get_container().llm


def get_document_store() -> DocumentStore:
    return get_container().document_store
