# Central Configuration System

The backend uses a layered configuration approach: Pydantic Settings models loaded from per-environment YAML files, with `.env` and OS environment variable overrides on top.

**File:** `backend/app/config/settings.py`

## Configuration Priority

Settings are resolved with a layered priority (highest wins):

1. **OS environment variables** — deployment overrides (e.g. `export AT_AZURE_DI__ENDPOINT=...`)
2. **`.env` file** — local development secrets (`backend/.env`)
3. **YAML config** — environment-specific structural defaults (`config/settings.{env}.yaml`)
4. **Pydantic field defaults** — code-level fallbacks in `settings.py`

This priority is enforced by `settings_customise_sources()` in `AppSettings`, which
reorders pydantic-settings sources so init kwargs (YAML) rank below env vars and `.env`.

## AppSettings Class

The root configuration model extends `pydantic_settings.BaseSettings`:

```python
class AppSettings(BaseSettings):
    app_name: str = "Auto Transcription"
    env: str = "dev"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000

    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    marker: MarkerConfig = Field(default_factory=MarkerConfig)
    azure_di: AzureDIConfig = Field(default_factory=AzureDIConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    hitl: HITLConfig = Field(default_factory=HITLConfig)

    model_config = {
        "env_prefix": "AT_",
        "env_nested_delimiter": "__",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }
```

## Configuration Models

### PipelineConfig

Controls which processing flow the system uses. This is the primary architectural switch — it determines which OCR engine is instantiated and which merge/confidence path executes in the workflow graph.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | `str` | `"azure_di"` | Pipeline mode: `"azure_di"` or `"marker_docling"` |

**Modes:**

| Mode | OCR Engine | Confidence Source | Cloud Dependency |
|------|-----------|-------------------|------------------|
| `azure_di` | `AzureDIOCRAdapter` | DI per-word scores + validation rules | Cloud API or disconnected container |
| `marker_docling` | `MarkerOCRAdapter` + `DoclingQualityAdapter` | Docling quality scores + validation rules | None (fully offline) |

### MarkerConfig

Controls the Marker OCR engine (only active when `pipeline.mode = marker_docling`):

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

Controls Azure Document Intelligence (active when `pipeline.mode = azure_di`):

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `endpoint` | `str` | `https://<resource>.cognitiveservices.azure.com` | Azure DI resource endpoint (cloud URL or `http://localhost:5000` for disconnected container) |
| `api_key` | `str` | (empty) | Azure DI API key |
| `features` | `list[str]` | `["barcodes", "keyValuePairs", "ocrHighResolution", "styleFont", "formulas", "languages"]` | Enabled DI features |

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
| `sync_url` | `str` | `postgresql://postgres:postgres@localhost:5432/autotranscription` | Sync database URL for schema management tasks |
| `echo` | `bool` | `False` | Enable SQL query logging |

### StorageConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `backend` | `str` | `filesystem` | Storage backend (`filesystem` or `azure_blob`) |
| `base_path` | `str` | `./data/documents` | Local filesystem storage path |
| `azure_connection_string` | `str` | (empty) | Azure Blob Storage connection string |
| `azure_container` | `str` | `documents` | Azure Blob container name |

### HITLConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `auto_approve_threshold` | `float` | `0.8` | Confidence score above which pages are auto-approved |
| `review_threshold` | `float` | `0.6` | Confidence score below which pages require human review |
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
pipeline:
  mode: azure_di

azure_di:
  features:
    - barcodes
    - keyValuePairs

marker:
  use_llm: true
  paginate_output: true
  extract_images: true
  ollama_base_url: "http://localhost:11434"
  ollama_model: "gemma2:9b"

llm:
  provider: ollama
  model: gemma2:9b

storage:
  backend: filesystem
  base_path: ./data/documents
```

The `pipeline.mode` field is the primary architectural switch. In `azure_di` mode, the `marker` section is still present but ignored by the Container — only the `azure_di` and `llm` sections are active. In `marker_docling` mode, the `azure_di` section is ignored and `marker` drives the OCR.

## Environment Variable Overrides

Environment variables use the `AT_` prefix with double underscores (`__`) for nested fields:

```bash
# Top-level fields
AT_ENV=staging
AT_DEBUG=false

# Pipeline mode
AT_PIPELINE__MODE=azure_di

# Azure Document Intelligence credentials
AT_AZURE_DI__ENDPOINT=https://my-resource.cognitiveservices.azure.com
AT_AZURE_DI__API_KEY=your-api-key-here

# LLM
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
3. Pass YAML values as keyword arguments (`init` source) to `AppSettings`
4. Pydantic-settings resolves all sources using `settings_customise_sources()`: OS env vars beat `.env`, which beats YAML init kwargs, which beat field defaults
5. Result is cached via `@lru_cache` — one `AppSettings` instance per process

## Pipeline Mode Switching

To switch between pipeline modes, change the `pipeline.mode` value. Everything downstream — which OCR adapter is instantiated, which merge node runs, which confidence formula is used — follows automatically.

**Via YAML:**

```yaml
# Use Azure Document Intelligence
pipeline:
  mode: azure_di

# Or use Marker + Docling (fully offline)
pipeline:
  mode: marker_docling
```

**Via environment variable:**

```bash
AT_PIPELINE__MODE=azure_di       # Azure DI
AT_PIPELINE__MODE=marker_docling # Marker + Docling
```

## Usage in Code

```python
from app.config.settings import get_settings

settings = get_settings()
print(settings.pipeline.mode)               # "azure_di"
print(settings.llm.provider)                # "ollama"
print(settings.hitl.auto_approve_threshold) # 0.9
```

The `Container` class in [Dependency Injection](./dependency-injection.md) uses `settings.pipeline.mode` to instantiate the correct adapter implementations.

## Related Pages

- [Dependency Injection](./dependency-injection.md) — How pipeline mode drives adapter selection
- [Local Setup](../../devops/local-setup.md) — Environment variable reference for local development
- [Azure DevOps Pipeline](../../devops/azure-devops-pipeline.md) — How settings differ per deployment environment
