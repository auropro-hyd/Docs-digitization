"""Dependency injection container.

Central wiring that connects ports to adapters based on the active configuration.
Swapping an engine = changing one value in settings.yaml; this module resolves
the concrete adapter at runtime.
"""

from __future__ import annotations

from functools import lru_cache

from app.config.settings import AppSettings, get_settings
from app.core.ports.llm import LLMProvider
from app.core.ports.notification import NotificationPort
from app.core.ports.ocr import OCREngine
from app.core.ports.quality import QualityScorer
from app.core.ports.storage import DocumentStore


def create_ocr_engine(engine_name: str, settings: AppSettings | None = None) -> OCREngine:
    settings = settings or get_settings()
    match engine_name:
        case "marker":
            from app.adapters.ocr.marker import MarkerOCRAdapter

            return MarkerOCRAdapter(settings.marker)
        case "azure_di":
            from app.adapters.ocr.azure_di import AzureDIOCRAdapter

            return AzureDIOCRAdapter(settings.azure_di)
        case _:
            raise ValueError(f"Unknown OCR engine: {engine_name}")


def create_quality_scorer(settings: AppSettings | None = None) -> QualityScorer:
    settings = settings or get_settings()
    match settings.ocr.quality_scorer:
        case "docling":
            from app.adapters.quality.docling import DoclingQualityAdapter

            return DoclingQualityAdapter()
        case _:
            raise ValueError(f"Unknown quality scorer: {settings.ocr.quality_scorer}")


def create_llm_provider(settings: AppSettings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    match settings.llm.provider:
        case "ollama":
            from app.adapters.llm.ollama import OllamaLLMAdapter

            return OllamaLLMAdapter(settings.llm)
        case "azure_openai":
            from app.adapters.llm.azure_openai import AzureOpenAILLMAdapter

            return AzureOpenAILLMAdapter(settings.llm)
        case _:
            raise ValueError(f"Unknown LLM provider: {settings.llm.provider}")


def create_document_store(settings: AppSettings | None = None) -> DocumentStore:
    settings = settings or get_settings()
    match settings.storage.backend:
        case "filesystem":
            from app.adapters.storage.filesystem import FileSystemAdapter

            return FileSystemAdapter(settings.storage)
        case "azure_blob":
            from app.adapters.storage.azure_blob import AzureBlobAdapter

            return AzureBlobAdapter(settings.storage)
        case _:
            raise ValueError(f"Unknown storage backend: {settings.storage.backend}")


def create_notification_port() -> NotificationPort:
    from app.adapters.notification.websocket import WebSocketNotifyAdapter

    return WebSocketNotifyAdapter()


class Container:
    """Lazy-initializing DI container. Holds singleton adapter instances."""

    def __init__(self, settings: AppSettings | None = None):
        self._settings = settings or get_settings()
        self._primary_ocr: OCREngine | None = None
        self._secondary_ocr: OCREngine | None = None
        self._quality_scorer: QualityScorer | None = None
        self._llm_provider: LLMProvider | None = None
        self._document_store: DocumentStore | None = None
        self._notification: NotificationPort | None = None

    @property
    def primary_ocr(self) -> OCREngine:
        if self._primary_ocr is None:
            self._primary_ocr = create_ocr_engine(self._settings.ocr.primary_engine, self._settings)
        return self._primary_ocr

    @property
    def secondary_ocr(self) -> OCREngine:
        if self._secondary_ocr is None:
            self._secondary_ocr = create_ocr_engine(self._settings.ocr.secondary_engine, self._settings)
        return self._secondary_ocr

    @property
    def quality_scorer(self) -> QualityScorer:
        if self._quality_scorer is None:
            self._quality_scorer = create_quality_scorer(self._settings)
        return self._quality_scorer

    @property
    def llm(self) -> LLMProvider:
        if self._llm_provider is None:
            self._llm_provider = create_llm_provider(self._settings)
        return self._llm_provider

    @property
    def document_store(self) -> DocumentStore:
        if self._document_store is None:
            self._document_store = create_document_store(self._settings)
        return self._document_store

    @property
    def notification(self) -> NotificationPort:
        if self._notification is None:
            self._notification = create_notification_port()
        return self._notification


@lru_cache
def get_container() -> Container:
    return Container()
