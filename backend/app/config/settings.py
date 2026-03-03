"""Central configuration loader.

Reads settings from per-environment YAML files (settings.dev.yaml, settings.staging.yaml,
settings.prod.yaml) overlaid with environment variables. A single source of truth for
which adapters are active and how they're configured.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class MarkerConfig(BaseModel):
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
    endpoint: str = "https://placeholder.cognitiveservices.azure.com"
    api_key: str = ""
    features: list[str] = Field(default_factory=lambda: ["barcodes", "keyValuePairs"])


class LLMConfig(BaseModel):
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "gemma2:9b"
    api_key: str = ""
    azure_endpoint: str = ""
    azure_deployment: str = ""


class DatabaseConfig(BaseModel):
    url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/autotranscription"
    sync_url: str = "postgresql://postgres:postgres@localhost:5432/autotranscription"
    echo: bool = False


class StorageConfig(BaseModel):
    backend: str = "filesystem"
    base_path: str = "./data/documents"
    azure_connection_string: str = ""
    azure_container: str = "documents"


class OCRConfig(BaseModel):
    primary_engine: str = "marker"
    secondary_engine: str = "azure_di"
    quality_scorer: str = "docling"


class HITLConfig(BaseModel):
    auto_approve_threshold: float = 0.9
    review_threshold: float = 0.7
    batch_review_enabled: bool = True


class AppSettings(BaseSettings):
    app_name: str = "Auto Transcription"
    env: str = "dev"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000

    ocr: OCRConfig = Field(default_factory=OCRConfig)
    marker: MarkerConfig = Field(default_factory=MarkerConfig)
    azure_di: AzureDIConfig = Field(default_factory=AzureDIConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    hitl: HITLConfig = Field(default_factory=HITLConfig)

    model_config = {"env_prefix": "AT_", "env_nested_delimiter": "__"}


def _load_yaml_config(env: str) -> dict:
    config_dir = Path(__file__).resolve().parent.parent.parent / "config"
    config_file = config_dir / f"settings.{env}.yaml"
    if config_file.exists():
        with open(config_file) as f:
            return yaml.safe_load(f) or {}
    return {}


@lru_cache
def get_settings() -> AppSettings:
    env = os.getenv("AT_ENV", "dev")
    yaml_config = _load_yaml_config(env)
    return AppSettings(env=env, **yaml_config)
