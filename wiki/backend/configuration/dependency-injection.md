# Dependency Injection Container

The DI container wires port interfaces to concrete adapter implementations based on the active pipeline mode. Switching between `azure_di` and `marker_docling` requires only a config change — no code modifications.

**Files:**
- `backend/app/config/container.py` — Container class and factory functions
- `backend/app/api/dependencies.py` — FastAPI `Depends()` integration

## Architecture

```
AppSettings (from settings.yaml + env vars)
      │
      │  settings.pipeline.mode
      ▼
Container (match/case on pipeline mode → lazy singleton instances)
      │
      ▼
FastAPI Depends() (injects into route handlers)
```

## Port Interfaces

The application defines port protocols (interfaces) that adapters must implement:

| Port | Module | Purpose |
|------|--------|---------|
| `OCREngine` | `app.core.ports.ocr` | OCR text extraction |
| `QualityScorer` | `app.core.ports.quality` | Extraction quality scoring (marker_docling mode) |
| `LLMProvider` | `app.core.ports.llm` | LLM inference |
| `DocumentStore` | `app.core.ports.storage` | Document file storage |
| `NotificationPort` | `app.core.ports.notification` | Real-time notifications |

## Container Class

The `Container` holds **lazily initialized singleton** instances of each adapter. The key architectural change is that OCR engine selection is driven by `settings.pipeline.mode` via a `match` statement — there is a single `ocr_engine` property instead of separate primary/secondary engines.

```python
class Container:
    def __init__(self, settings: AppSettings | None = None):
        self._settings = settings or get_settings()
        self._ocr_engine: OCREngine | None = None
        self._quality_scorer: QualityScorer | None = None
        self._llm_provider: LLMProvider | None = None
        self._document_store: DocumentStore | None = None
        self._notification: NotificationPort | None = None

    @property
    def pipeline_mode(self) -> str:
        return self._settings.pipeline.mode

    @property
    def ocr_engine(self) -> OCREngine:
        if self._ocr_engine is None:
            match self._settings.pipeline.mode:
                case "azure_di":
                    from app.adapters.ocr.azure_di import AzureDIOCRAdapter
                    self._ocr_engine = AzureDIOCRAdapter(self._settings.azure_di)
                case "marker_docling":
                    from app.adapters.ocr.marker import MarkerOCRAdapter
                    self._ocr_engine = MarkerOCRAdapter(self._settings.marker)
                case _:
                    raise ValueError(f"Unknown pipeline mode: {self._settings.pipeline.mode}")
        return self._ocr_engine
```

### Pipeline Mode Resolution

The `ocr_engine` property resolves to a different adapter depending on `settings.pipeline.mode`:

| Pipeline Mode | `ocr_engine` resolves to | Config Used |
|---------------|-------------------------|-------------|
| `azure_di` | `AzureDIOCRAdapter(settings.azure_di)` | `AzureDIConfig` |
| `marker_docling` | `MarkerOCRAdapter(settings.marker)` | `MarkerConfig` |

The `quality_scorer` property always returns `DoclingQualityAdapter` but is only used in `marker_docling` mode — the workflow graph does not invoke it in `azure_di` mode.

### Container Properties

| Property | Resolved Adapter | Config Field |
|----------|-----------------|-------------|
| `pipeline_mode` | Returns `str` — the active mode | `pipeline.mode` |
| `ocr_engine` | `AzureDIOCRAdapter` or `MarkerOCRAdapter` (based on pipeline mode) | `pipeline.mode` → `azure_di.*` or `marker.*` |
| `quality_scorer` | `DoclingQualityAdapter` (marker_docling mode only) | — |
| `llm` | `OllamaLLMAdapter` or `AzureOpenAILLMAdapter` | `llm.provider` |
| `document_store` | `FileSystemAdapter` or `AzureBlobAdapter` | `storage.backend` |
| `notification` | `WebSocketNotifyAdapter` | (always WebSocket) |

### `get_container()`

Returns a cached `Container` singleton:

```python
@lru_cache
def get_container() -> Container:
    return Container()
```

## Factory Functions

Non-pipeline adapters still use standalone factory functions with `match/case` dispatch:

### `create_llm_provider(settings?)`

Dispatches on `settings.llm.provider`:

| Config Value | Adapter | Settings Model |
|-------------|---------|----------------|
| `ollama` | `OllamaLLMAdapter` | `LLMConfig` |
| `azure_openai` | `AzureOpenAILLMAdapter` | `LLMConfig` |

### `create_document_store(settings?)`

Dispatches on `settings.storage.backend`:

| Config Value | Adapter | Settings Model |
|-------------|---------|----------------|
| `filesystem` | `FileSystemAdapter` | `StorageConfig` |
| `azure_blob` | `AzureBlobAdapter` | `StorageConfig` |

### `create_notification_port()`

Currently always returns `WebSocketNotifyAdapter`. No configuration dispatch needed — WebSocket is the only notification mechanism.

## FastAPI Integration

**File:** `backend/app/api/dependencies.py`

Exposes simple functions that route handlers can use with `Depends()`:

```python
from app.config.container import Container, get_container

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
```

Usage in a route handler:

```python
from fastapi import Depends
from app.api.dependencies import get_ocr_engine
from app.core.ports.ocr import OCREngine

@router.post("/process")
async def process_document(ocr: OCREngine = Depends(get_ocr_engine)):
    result = await ocr.extract(document)
    return result
```

The injected `ocr` is automatically the correct adapter for the active pipeline mode — `AzureDIOCRAdapter` in `azure_di` mode, `MarkerOCRAdapter` in `marker_docling` mode. No branching needed in route handlers.

## Swapping the Pipeline Mode

To switch between Azure DI and Marker+Docling, change a single config value:

### Via YAML

```yaml
# config/settings.dev.yaml
pipeline:
  mode: marker_docling   # switch from azure_di to marker_docling
```

### Via Environment Variable

```bash
AT_PIPELINE__MODE=marker_docling
```

No changes needed in route handlers, the Container class, or FastAPI dependencies — the new adapter flows through automatically via the `match` statement in `Container.ocr_engine`.

## Adding a New Pipeline Mode

To add a new pipeline mode (e.g., `tesseract`):

### 1. Implement the Port Protocol

```python
# backend/app/adapters/ocr/tesseract.py
from app.core.ports.ocr import OCREngine

class TesseractOCRAdapter(OCREngine):
    def __init__(self, config):
        self.config = config

    async def extract(self, document) -> ExtractedResult:
        # ... implementation
```

### 2. Add a Case in the Container

Add a new `match` case to `Container.ocr_engine` in `container.py`:

```python
@property
def ocr_engine(self) -> OCREngine:
    if self._ocr_engine is None:
        match self._settings.pipeline.mode:
            case "azure_di":
                from app.adapters.ocr.azure_di import AzureDIOCRAdapter
                self._ocr_engine = AzureDIOCRAdapter(self._settings.azure_di)
            case "marker_docling":
                from app.adapters.ocr.marker import MarkerOCRAdapter
                self._ocr_engine = MarkerOCRAdapter(self._settings.marker)
            case "tesseract":
                from app.adapters.ocr.tesseract import TesseractOCRAdapter
                self._ocr_engine = TesseractOCRAdapter(self._settings.tesseract)
            case _:
                raise ValueError(...)
    return self._ocr_engine
```

### 3. Add a Merge Node and Flow Edges

Add a corresponding `merge_tesseract_results` node in `nodes.py` and wire the flow edges in `document_graph.py`.

### 4. Set the Configuration

```bash
AT_PIPELINE__MODE=tesseract
```

## Adapter Registry

Current adapter implementations:

| Port | Adapter | Module |
|------|---------|--------|
| OCREngine | `MarkerOCRAdapter` | `app.adapters.ocr.marker` |
| OCREngine | `AzureDIOCRAdapter` | `app.adapters.ocr.azure_di` |
| QualityScorer | `DoclingQualityAdapter` | `app.adapters.quality.docling` |
| LLMProvider | `OllamaLLMAdapter` | `app.adapters.llm.ollama` |
| LLMProvider | `AzureOpenAILLMAdapter` | `app.adapters.llm.azure_openai` |
| DocumentStore | `FileSystemAdapter` | `app.adapters.storage.filesystem` |
| DocumentStore | `AzureBlobAdapter` | `app.adapters.storage.azure_blob` |
| NotificationPort | `WebSocketNotifyAdapter` | `app.adapters.notification.websocket` |
| NotificationPort | `PgNotifyAdapter` | `app.adapters.notification.pg_notify` |

## Related Pages

- [Settings](./settings.md) — Configuration models that drive adapter selection
- [Local Setup](../../devops/local-setup.md) — Environment variables for local development
- [WebSocket Streaming](../../frontend/websocket-streaming.md) — The notification adapter in action
