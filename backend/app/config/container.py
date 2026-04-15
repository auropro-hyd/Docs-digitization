"""Dependency injection container.

Central wiring that connects ports to adapters based on the active configuration.
Pipeline mode determines which OCR engine(s) to instantiate:
  - azure_di:       Only AzureDIOCRAdapter (cloud or disconnected container)
  - marker_docling: MarkerOCRAdapter + DoclingQualityAdapter
"""

from __future__ import annotations

from functools import lru_cache

from app.config.settings import AppSettings, ComplianceConfig, ComplianceLLMConfig, LLMConfig, get_settings
from app.core.ports.llm import LLMProvider
from app.core.ports.notification import NotificationPort
from app.core.ports.ocr import OCREngine
from app.core.ports.quality import QualityScorer
from app.core.ports.storage import DocumentStore
from app.core.ports.vlm import VLMProvider


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


def _resolve_compliance_llm_config(
    comp: ComplianceConfig,
    override: ComplianceLLMConfig,
    default_model: str,
    default_deployment: str,
    fallback: LLMConfig,
) -> LLMConfig:
    """Build an LLMConfig for a compliance component by cascading overrides.

    Cascade: per-component override → ComplianceConfig defaults → main LLMConfig fallback.
    """
    return LLMConfig(
        provider=override.provider or comp.llm_provider or fallback.provider,
        api_key=override.api_key or comp.api_key or fallback.api_key,
        azure_endpoint=override.azure_endpoint or comp.azure_endpoint or fallback.azure_endpoint,
        azure_deployment=override.azure_deployment or default_deployment or fallback.azure_deployment,
        model=override.model or default_model or fallback.model,
        base_url=fallback.base_url,
        azure_max_rpm=fallback.azure_max_rpm,
        azure_max_concurrent=fallback.azure_max_concurrent,
    )


def create_compliance_llm(
    role: str, settings: AppSettings | None = None,
) -> LLMProvider:
    """Create an LLM provider for a compliance component.

    Args:
        role: ``"evaluator"`` or ``"orchestrator"``.
    """
    settings = settings or get_settings()
    comp = settings.compliance
    fallback = settings.llm

    if role == "evaluator":
        cfg = _resolve_compliance_llm_config(
            comp, comp.evaluator_llm, comp.evaluator_model, comp.evaluator_deployment, fallback,
        )
    elif role == "orchestrator":
        cfg = _resolve_compliance_llm_config(
            comp, comp.orchestrator_llm, comp.orchestrator_model, comp.orchestrator_deployment, fallback,
        )
    elif role == "cross_page":
        cfg = _resolve_compliance_llm_config(
            comp,
            ComplianceLLMConfig(),
            comp.cross_page_model or comp.evaluator_model,
            comp.cross_page_deployment or comp.evaluator_deployment,
            fallback,
        )
    else:
        raise ValueError(f"Unknown compliance LLM role: {role}")

    match cfg.provider:
        case "ollama":
            from app.adapters.llm.ollama import OllamaLLMAdapter
            return OllamaLLMAdapter(cfg)
        case "azure_openai":
            from app.adapters.llm.azure_openai import AzureOpenAILLMAdapter
            return AzureOpenAILLMAdapter(cfg)
        case _:
            raise ValueError(f"Unknown compliance LLM provider: {cfg.provider}")


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


def create_vlm_provider(settings: AppSettings | None = None) -> VLMProvider:
    """Create a VLM provider based on configuration.

    Returns the concrete adapter for the configured provider.
    Caller must check ``settings.vlm.enabled`` before calling.
    """
    settings = settings or get_settings()
    match settings.vlm.provider:
        case "gemini":
            from app.adapters.vlm.gemini import GeminiVLMAdapter

            return GeminiVLMAdapter(settings.vlm)
        case "vllm":
            from app.adapters.vlm.vllm_openai import VLLMOpenAIVLMAdapter

            return VLLMOpenAIVLMAdapter(settings.vlm)
        case _:
            raise ValueError(f"Unknown VLM provider: {settings.vlm.provider}")


def create_notification_port() -> NotificationPort:
    from app.adapters.notification.websocket import WebSocketNotifyAdapter

    return WebSocketNotifyAdapter()


class Container:
    """Lazy-initializing DI container. Holds singleton adapter instances.

    Pipeline mode controls which OCR engines are created:
      azure_di:       self.ocr_engine → AzureDIOCRAdapter
      marker_docling: self.ocr_engine → MarkerOCRAdapter, self.quality_scorer → DoclingQualityAdapter
    """

    def __init__(self, settings: AppSettings | None = None):
        self._settings = settings or get_settings()
        self._ocr_engine: OCREngine | None = None
        self._quality_scorer: QualityScorer | None = None
        self._llm_provider: LLMProvider | None = None
        self._vlm_provider: VLMProvider | None = None
        self._vlm_checked: bool = False
        self._document_store: DocumentStore | None = None
        self._notification: NotificationPort | None = None
        self._compliance_evaluator_llm: LLMProvider | None = None
        self._compliance_orchestrator_llm: LLMProvider | None = None
        self._compliance_cross_page_llm: LLMProvider | None = None

    @property
    def pipeline_mode(self) -> str:
        return self._settings.pipeline.mode

    @property
    def ocr_engine(self) -> OCREngine:
        """Primary OCR engine for the active pipeline mode."""
        if self._ocr_engine is None:
            match self._settings.pipeline.mode:
                case "azure_di":
                    from app.adapters.ocr.azure_di import AzureDIOCRAdapter

                    self._ocr_engine = AzureDIOCRAdapter(self._settings.azure_di)
                case "marker_docling":
                    from app.adapters.ocr.marker import MarkerOCRAdapter

                    self._ocr_engine = MarkerOCRAdapter(self._settings.marker)
                case "datalab":
                    from app.adapters.ocr.datalab import DatalabOCRAdapter

                    self._ocr_engine = DatalabOCRAdapter(self._settings.datalab)
                case _:
                    raise ValueError(f"Unknown pipeline mode: {self._settings.pipeline.mode}")
        return self._ocr_engine

    @property
    def quality_scorer(self) -> QualityScorer:
        """Quality scorer (only used in marker_docling mode)."""
        if self._quality_scorer is None:
            from app.adapters.quality.docling import DoclingQualityAdapter

            self._quality_scorer = DoclingQualityAdapter()
        return self._quality_scorer

    @property
    def llm(self) -> LLMProvider:
        if self._llm_provider is None:
            self._llm_provider = create_llm_provider(self._settings)
        return self._llm_provider

    @property
    def vlm(self) -> VLMProvider | None:
        """VLM provider, or ``None`` when VLM is disabled."""
        if not self._vlm_checked:
            self._vlm_checked = True
            if self._settings.vlm.enabled:
                self._vlm_provider = create_vlm_provider(self._settings)
        return self._vlm_provider

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

    @property
    def compliance_evaluator_llm(self) -> LLMProvider:
        if self._compliance_evaluator_llm is None:
            self._compliance_evaluator_llm = create_compliance_llm("evaluator", self._settings)
        return self._compliance_evaluator_llm

    @property
    def compliance_orchestrator_llm(self) -> LLMProvider:
        if self._compliance_orchestrator_llm is None:
            self._compliance_orchestrator_llm = create_compliance_llm("orchestrator", self._settings)
        return self._compliance_orchestrator_llm

    @property
    def compliance_cross_page_llm(self) -> LLMProvider:
        if self._compliance_cross_page_llm is None:
            self._compliance_cross_page_llm = create_compliance_llm("cross_page", self._settings)
        return self._compliance_cross_page_llm


@lru_cache
def get_container() -> Container:
    return Container()
