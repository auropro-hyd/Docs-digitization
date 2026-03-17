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


class LLMConfig(BaseModel):
    """LLM provider settings (Ollama for on-prem, Azure OpenAI for cloud)."""

    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "gemma2:9b"
    api_key: str = ""
    azure_endpoint: str = ""
    azure_deployment: str = ""
    # Azure OpenAI rate limiting (per deployment); stay under deployment RPM limit
    azure_max_rpm: int = 800  # max requests/min; deployment allows 1000 RPM at 1M TPM
    azure_max_concurrent: int = 3  # keep concurrency moderate to avoid token-burst 429s


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

    rule_batch_size: int = 7
    max_concurrent_batches: int = 4
    batch_by_category: bool = True
    llm_timeout: int = 120

    enable_cross_page: bool = True
    cross_page_model: str = ""
    cross_page_deployment: str = ""
    auto_discover_checks: bool = True
    max_section_chars: int = 15000


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
    llm: LLMConfig = Field(default_factory=LLMConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    hitl: HITLConfig = Field(default_factory=HITLConfig)
    compliance: ComplianceConfig = Field(default_factory=ComplianceConfig)

    model_config = {
        "env_prefix": "AT_",
        "env_nested_delimiter": "__",
        "env_file": ".env",
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
