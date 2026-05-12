"""Central configuration loader.

Priority (highest → lowest):
  1. OS environment variables   – deployment overrides (AT_AZURE_DI__ENDPOINT, etc.)
  2. .env file                  – local development secrets
  3. YAML config file           – environment-specific structural defaults
  4. Pydantic field defaults    – code-level fallbacks
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource


class PipelineConfig(BaseModel):
    """Processing flow selection.

    azure_di:       Azure Document Intelligence — cloud API or disconnected container.
    marker_docling: Marker OCR + Docling quality scoring — fully offline.
    datalab:        Data Lab (Chandra) OCR — superior handwriting + tables.
    """

    mode: str = "azure_di"


class MarkerConfig(BaseModel):
    """Marker OCR engine settings (used in marker_docling mode)."""

    use_llm: bool = True
    paginate_output: bool = True
    extract_images: bool = True
    no_merge_tables_across_pages: bool = False
    table_height_threshold: float = 0.6
    max_table_rows: int = 175
    html_tables_in_markdown: bool = False
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma2:9b"


class AzureDIConfig(BaseModel):
    """Azure Document Intelligence settings.

    Same adapter for cloud API and disconnected container — only endpoint differs:
      Cloud:     https://<resource>.cognitiveservices.azure.com
      Container: http://localhost:5000
    """

    endpoint: str = "https://placeholder.cognitiveservices.azure.com"
    api_key: str = ""
    features: list[str] = Field(default_factory=lambda: [
        "barcodes",
        "keyValuePairs",
        "ocrHighResolution",
        "styleFont",
        "formulas",
        "languages",
    ])
    feature_profiles: dict[str, list[str]] = Field(default_factory=lambda: {
        "default": ["barcodes", "keyValuePairs", "ocrHighResolution", "styleFont", "languages"],
        "bpr_core": ["barcodes", "keyValuePairs", "ocrHighResolution", "styleFont", "languages", "formulas"],
        "manufacturing_checklists": ["barcodes", "keyValuePairs", "ocrHighResolution", "styleFont", "languages"],
        "instrument_data_reports": [
            "barcodes",
            "keyValuePairs",
            "ocrHighResolution",
            "styleFont",
            "languages",
            "formulas",
        ],
        "analysis_reports": ["barcodes", "keyValuePairs", "ocrHighResolution", "styleFont", "languages"],
    })
    quality_gate_enabled: bool = True
    quality_gate_block_on_critical: bool = False
    quality_gate_sample_pages: int = 3
    quality_gate_min_render_width: int = 1200
    quality_gate_min_render_height: int = 1600
    quality_gate_min_contrast_std: float = 26.0
    query_fields_enabled: bool = True
    drift_threshold_correction_rate: float = 0.08
    drift_threshold_critical_error_rate: float = 0.03
    drift_min_corrections_for_trigger: int = 20
    canary_enabled: bool = True
    canary_query_fields_percent: int = 50
    rollback_min_quality_f1_delta: float = -0.01
    rollback_max_latency_ms_delta: float = 150.0
    rollback_max_cost_usd_delta: float = 0.02
    custom_model_enabled: bool = False
    custom_model_shadow_enabled: bool = True
    analyze_timeout_seconds: int = 900
    progress_poll_interval_seconds: int = 2
    progress_heartbeat_seconds: int = 30
    submit_max_retries: int = 3
    submit_retry_base_delay: float = 5.0
    chunk_pages: int = 50

    def features_for_profile(self, profile: str | None) -> list[str]:
        key = (profile or "default").strip().lower()
        return list(self.feature_profiles.get(key) or self.feature_profiles.get("default") or self.features)


class DatalabConfig(BaseModel):
    """Data Lab (Chandra) OCR settings.

    Chandra excels at handwriting recognition, complex tables, checkbox
    detection, and form understanding.  Uses the datalab-python-sdk.
    """

    api_key: str = ""
    base_url: str = "https://www.datalab.to"
    timeout: int = 300
    mode: str = "accurate"  # "fast" | "balanced" | "accurate"
    paginate: bool = True
    max_pages: int | None = None
    extras: str = "new_block_types,table_row_bboxes,chart_understanding"
    output_format: str = "markdown"
    # Datalab emits cropped signature / handwriting / figure
    # regions as ``<img data-bbox=... src="HASH_img.jpg"/>`` tags
    # in the markdown when image extraction is enabled. We want
    # these binaries so the frontend can render signature
    # crops alongside the ``[Signature]`` text marker
    # (PR #35 — Akhilesh reported "image is broken" on
    # 2538105061.pdf because the markdown referenced hashes
    # that were never written to disk). The pipeline persists
    # them to ``<doc_dir>/images/<hash>_img.jpg`` and serves
    # them via ``/api/documents/{doc_id}/images/{filename}``.
    disable_image_extraction: bool = False
    disable_image_captions: bool = True
    token_efficient_markdown: bool = False
    page_range: str | None = None
    max_polls: int = 300
    poll_interval: float = 1.0
    chunk_pages: int = 50
    max_concurrent_chunks: int = 8
    submit_max_retries: int = 3
    submit_retry_base_delay: float = 5.0

    # Quality enhancements
    use_llm: bool = True
    force_ocr: bool = False
    strip_existing_ocr: bool = False

    # Structured extraction (Extract API)
    enable_extraction: bool = True
    extraction_schema_family: str = "bpr_core"
    extraction_schema: dict = Field(default_factory=dict)
    save_checkpoint: bool = True

    # Signature enrichment — deterministic post-OCR pass that
    # synthesizes ``[Signature]`` markers in table cells where
    # Datalab's classifier missed the signature stroke but
    # context (signature-named column + cell with handwritten
    # content) makes presence obvious. See
    # :mod:`app.adapters.ocr.signature_enricher`.
    # Defaults on; set ``AT_DATALAB__SIGNATURE_ENRICHMENT=false``
    # to disable and run the raw Datalab output (e.g. for
    # diagnostic A/B comparison against the classifier).
    signature_enrichment: bool = True

    # Aggressive (L4) layer: synthesizes ``[Signature]`` from
    # column-header + date-only-cell signals ALONE, without
    # requiring a Handwriting/Signature block in Datalab's JSON
    # tree. Necessary because diagnostic on real BPCRs shows
    # Datalab returns ``handwritten_count=0`` per page even
    # when ``[Signature]`` markers ARE in the markdown — the
    # JSON tree is structurally unreliable as block evidence.
    # Defaults on so the new-doc symptom is fixed by default;
    # set to ``false`` for strict "trust Datalab classifier
    # only" semantics. Confidence floor 0.30 keeps L4
    # findings in the ``uncertain`` band downstream.
    signature_enrichment_aggressive: bool = True

    # Bounding box enrichment via JSON output
    fetch_block_bboxes: bool = True


class FeedbackConfig(BaseModel):
    """OCR correction learning settings (Tier 1: runtime post-correction)."""

    auto_correct_enabled: bool = False
    min_correction_occurrences: int = 3
    min_correction_source_docs: int = 2
    min_correction_confidence: float = 0.8
    rebuild_on_review_save: bool = True
    correction_store_path: str = "data/corrections/global_corrections.json"


class LLMConfig(BaseModel):
    """LLM provider settings (Ollama for on-prem, Azure OpenAI for cloud)."""

    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "gemma2:9b"
    api_key: str = ""
    azure_endpoint: str = ""
    azure_deployment: str = ""
    # Azure OpenAI rate limiting (per deployment); stay under deployment RPM limit
    azure_max_rpm: int = 800
    azure_max_concurrent: int = 25


class DatabaseConfig(BaseModel):
    """PostgreSQL connection settings."""

    url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/autotranscription"
    sync_url: str = "postgresql://postgres:postgres@localhost:5432/autotranscription"
    echo: bool = False


class StorageConfig(BaseModel):
    """Document storage backend settings."""

    backend: str = "filesystem"
    base_path: str = "./data/documents"
    azure_connection_string: str = ""
    azure_container: str = "documents"


class HITLConfig(BaseModel):
    """Human-in-the-loop confidence thresholds.

    Tiers:
      >= auto_approve_threshold (0.8): auto-approved, high confidence
      >= review_threshold (0.6):       medium confidence, needs approval
      < review_threshold (0.6):        low confidence, needs approval
    """

    auto_approve_threshold: float = 0.8
    review_threshold: float = 0.6
    batch_review_enabled: bool = True


class ComplianceLLMConfig(BaseModel):
    """Per-component LLM overrides. Empty strings fall back to ComplianceConfig defaults."""

    provider: str = ""
    model: str = ""
    azure_endpoint: str = ""
    azure_deployment: str = ""
    api_key: str = ""


class VLMConfig(BaseModel):
    """Vision Language Model provider settings.

    Enables visual compliance checks (strikethroughs, ink color, correction
    fluid, wet signatures, stamps, watermarks, etc.) that OCR text cannot assess.

    Providers:
      gemini: Google Generative Language API (cloud, no GPU needed)
      vllm:   Self-hosted container via vLLM (Qwen3-VL, InternVL3, etc.)
    """

    enabled: bool = False
    provider: str = "gemini"  # "gemini" | "vllm"

    # Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_project: str = ""

    # vLLM / OpenAI-compatible
    vllm_base_url: str = "http://localhost:8200/v1"
    vllm_model: str = "Qwen/Qwen3-VL-8B-Instruct"
    vllm_api_key: str = ""

    # Image processing
    max_image_width: int = 2048
    max_image_height: int = 2048
    image_format: str = "png"  # "png" | "jpeg"
    jpeg_quality: int = 95
    render_scale: float = 2.0
    store_page_images: bool = True

    # Rate limiting
    max_rpm: int = 60
    max_concurrent: int = 5


class ComplianceConfig(BaseModel):
    """Compliance audit agent settings.

    Mixed-model defaults:
      evaluator  → GPT-4.1-mini  (instruction following + structured output)
      orchestrator → GPT-5-mini  (reasoning + synthesis)
    """

    llm_provider: str = "azure_openai"
    api_key: str = ""
    azure_endpoint: str = ""

    evaluator_model: str = "gpt-4.1-mini"
    evaluator_deployment: str = "gpt-4.1-mini"
    orchestrator_model: str = "gpt-4.1-mini"
    orchestrator_deployment: str = "gpt-4.1-mini"

    evaluator_llm: ComplianceLLMConfig = Field(default_factory=ComplianceLLMConfig)
    orchestrator_llm: ComplianceLLMConfig = Field(default_factory=ComplianceLLMConfig)

    rule_batch_size: int = 15
    max_concurrent_batches: int = 25
    batch_by_category: bool = True
    llm_timeout: int = 120

    applicability_mode: str = "static"  # "static" | "llm"

    enable_cross_page: bool = True
    cross_page_model: str = ""
    cross_page_deployment: str = ""
    auto_discover_checks: bool = True
    max_section_chars: int = 15000

    # Vision evaluation
    vlm_evaluation_enabled: bool = True
    vlm_batch_size: int = 5
    vlm_timeout: int = 180
    vlm_fallback_to_text: bool = True


class AppSettings(BaseSettings):
    """Application settings with layered configuration.

    Priority (highest → lowest):
      1. OS environment variables
      2. .env file
      3. YAML config (settings.{env}.yaml)
      4. Field defaults
    """

    app_name: str = "Auto Transcription"
    env: str = "dev"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    marker: MarkerConfig = Field(default_factory=MarkerConfig)
    azure_di: AzureDIConfig = Field(default_factory=AzureDIConfig)
    datalab: DatalabConfig = Field(default_factory=DatalabConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    vlm: VLMConfig = Field(default_factory=VLMConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    hitl: HITLConfig = Field(default_factory=HITLConfig)
    compliance: ComplianceConfig = Field(default_factory=ComplianceConfig)
    feedback: FeedbackConfig = Field(default_factory=FeedbackConfig)

    # Resolve ``.env`` to an absolute path relative to the backend package
    # root so the file is found regardless of which directory uvicorn is
    # invoked from. Without this, running ``uvicorn`` from the repo root
    # silently misses ``backend/.env`` and every secret falls back to
    # empty defaults — the symptom is a 400 INVALID_ARGUMENT from the
    # downstream LLM/VLM provider.
    _BACKEND_ROOT = Path(__file__).resolve().parents[2]

    model_config = {
        "env_prefix": "AT_",
        "env_nested_delimiter": "__",
        "env_file": str(_BACKEND_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
        **kwargs: Any,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Override source priority: env vars > .env > YAML (init kwargs) > defaults."""
        return (env_settings, dotenv_settings, init_settings, file_secret_settings)


def _load_yaml_config(env: str) -> dict:
    """Load environment-specific YAML config file."""
    config_dir = Path(__file__).resolve().parent.parent.parent / "config"
    config_file = config_dir / f"settings.{env}.yaml"
    if config_file.exists():
        with open(config_file) as f:
            return yaml.safe_load(f) or {}
    return {}


@lru_cache
def get_settings() -> AppSettings:
    """Build settings with layered priority.

    YAML values are passed as init kwargs but ranked BELOW env vars
    and .env via settings_customise_sources().
    """
    env = os.getenv("AT_ENV", "dev")
    yaml_config = _load_yaml_config(env)
    return AppSettings(env=env, **yaml_config)
