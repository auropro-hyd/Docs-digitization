# Central Configuration System

The backend uses a layered configuration approach: Pydantic Settings models loaded from per-environment YAML files, with environment variable overrides on top.

**File:** `backend/app/config/settings.py`

## Architecture

```
Environment variable (AT_LLM__PROVIDER=azure_openai)
          │  highest priority — overrides everything
          ▼
Per-environment YAML (config/settings.dev.yaml)
          │  merged into Pydantic model
          ▼
Pydantic defaults (defined in code)
          │  lowest priority — fallback values
          ▼
AppSettings instance (cached via @lru_cache)
```

## AppSettings Class

The root configuration model extends `pydantic_settings.BaseSettings`:

```python
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
```

## Configuration Models

### MarkerConfig

Controls the Marker OCR engine:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `use_llm` | `bool` | `True` | Enable LLM-assisted extraction |
| `paginate_output` | `bool` | `True` | Split output by page |
| `extract_images` | `bool` | `True` | Extract embedded images |
| `no_merge_tables_across_pages` | `bool` | `False` | Prevent table merging across page breaks |
| `table_height_threshold` | `float` | `0.6` | Height ratio threshold for table detection |
| `max_table_rows` | `int` | `175` | Maximum rows per table |
| `html_tables_in_markdown` | `bool` | `False` | Use HTML table syntax in markdown output |
| `ollama_base_url` | `str` | `http://localhost:11434` | Ollama server URL |
| `ollama_model` | `str` | `gemma2:9b` | Ollama model for Marker LLM features |

### AzureDIConfig

Controls Azure Document Intelligence:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `endpoint` | `str` | (placeholder) | Azure DI resource endpoint |
| `api_key` | `str` | (empty) | Azure DI API key |
| `features` | `list[str]` | `["barcodes", "keyValuePairs"]` | Enabled DI features |

### LLMConfig

Controls the LLM provider for compliance analysis and other AI tasks:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | `str` | `ollama` | Provider name (`ollama` or `azure_openai`) |
| `base_url` | `str` | `http://localhost:11434` | Provider base URL |
| `model` | `str` | `gemma2:9b` | Model name |
| `api_key` | `str` | (empty) | API key (for Azure OpenAI) |
| `azure_endpoint` | `str` | (empty) | Azure OpenAI endpoint |
| `azure_deployment` | `str` | (empty) | Azure OpenAI deployment name |

### DatabaseConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | `str` | `postgresql+asyncpg://postgres:postgres@localhost:5432/autotranscription` | Async database URL |
| `sync_url` | `str` | `postgresql://postgres:postgres@localhost:5432/autotranscription` | Sync database URL (migrations) |
| `echo` | `bool` | `False` | Enable SQL query logging |

### StorageConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `backend` | `str` | `filesystem` | Storage backend (`filesystem` or `azure_blob`) |
| `base_path` | `str` | `./data/documents` | Local filesystem storage path |
| `azure_connection_string` | `str` | (empty) | Azure Blob Storage connection string |
| `azure_container` | `str` | `documents` | Azure Blob container name |

### OCRConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `primary_engine` | `str` | `marker` | Primary OCR engine name |
| `secondary_engine` | `str` | `azure_di` | Secondary OCR engine name |
| `quality_scorer` | `str` | `docling` | Quality scoring engine name |

### HITLConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `auto_approve_threshold` | `float` | `0.9` | Confidence score above which pages are auto-approved |
| `review_threshold` | `float` | `0.7` | Confidence score below which pages require human review |
| `batch_review_enabled` | `bool` | `True` | Enable batch review mode |

## Per-Environment YAML Files

Located in `backend/config/`:

| File | Environment | Purpose |
|------|-------------|---------|
| `settings.dev.yaml` | `dev` | Local development defaults |
| `settings.staging.yaml` | `staging` | Azure staging configuration |
| `settings.prod.yaml` | `prod` | Production configuration |
| `settings.test.yaml` | `test` | Test suite configuration |

The active environment is determined by the `AT_ENV` environment variable (default: `dev`).

Example `settings.dev.yaml`:

```yaml
debug: true
ocr:
  primary_engine: marker
  secondary_engine: azure_di
  quality_scorer: docling
llm:
  provider: ollama
  model: gemma2:9b
storage:
  backend: filesystem
  base_path: ./data/documents
```

## Environment Variable Overrides

Environment variables use the `AT_` prefix with double underscores (`__`) for nested fields:

```bash
# Top-level fields
AT_ENV=staging
AT_DEBUG=false

# Nested fields (double underscore = nesting)
AT_LLM__PROVIDER=azure_openai
AT_LLM__AZURE_ENDPOINT=https://my-openai.openai.azure.com
AT_LLM__AZURE_DEPLOYMENT=gpt-4

AT_DATABASE__URL=postgresql+asyncpg://user:pass@dbhost:5432/autotranscription

AT_STORAGE__BACKEND=azure_blob
AT_STORAGE__AZURE_CONNECTION_STRING=DefaultEndpointsProtocol=https;...

AT_HITL__AUTO_APPROVE_THRESHOLD=0.85
```

## Loading Mechanism

```python
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
```

1. Read `AT_ENV` from environment (default `dev`)
2. Load `config/settings.{env}.yaml` if it exists
3. Pass YAML values as keyword arguments to `AppSettings`
4. Pydantic Settings automatically overlays any `AT_*` environment variables
5. Result is cached via `@lru_cache` — one `AppSettings` instance per process

## Usage in Code

```python
from app.config.settings import get_settings

settings = get_settings()
print(settings.llm.provider)        # "ollama"
print(settings.hitl.auto_approve_threshold)  # 0.9
```

The `Container` class in [Dependency Injection](./dependency-injection.md) uses these settings to instantiate the correct adapter implementations.

## Related Pages

- [Dependency Injection](./dependency-injection.md) — How settings drive adapter selection
- [Local Setup](../../devops/local-setup.md) — Environment variable reference for local development
- [Azure DevOps Pipeline](../../devops/azure-devops-pipeline.md) — How settings differ per deployment environment
