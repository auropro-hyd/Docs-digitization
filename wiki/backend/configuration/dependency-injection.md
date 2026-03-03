# Dependency Injection Container

The DI container wires port interfaces to concrete adapter implementations based on the active configuration. Swapping an engine requires only a config change — no code modifications.

**Files:**
- `backend/app/config/container.py` — Container class and factory functions
- `backend/app/api/dependencies.py` — FastAPI `Depends()` integration

## Architecture

```
AppSettings (from settings.yaml + env vars)
      │
      ▼
Factory Functions (match/case on config values)
      │
      ▼
Container (lazy singleton instances)
      │
      ▼
FastAPI Depends() (injects into route handlers)
```

## Port Interfaces

The application defines port protocols (interfaces) that adapters must implement:

| Port | Module | Purpose |
|------|--------|---------|
| `OCREngine` | `app.core.ports.ocr` | OCR text extraction |
| `QualityScorer` | `app.core.ports.quality` | Extraction quality scoring |
| `LLMProvider` | `app.core.ports.llm` | LLM inference |
| `DocumentStore` | `app.core.ports.storage` | Document file storage |
| `NotificationPort` | `app.core.ports.notification` | Real-time notifications |

## Factory Functions

Each factory function uses Python's `match/case` to dispatch based on configuration values:

### `create_ocr_engine(engine_name, settings?)`

```python
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
```

| Config Value | Adapter | Settings Model |
|-------------|---------|----------------|
| `marker` | `MarkerOCRAdapter` | `MarkerConfig` |
| `azure_di` | `AzureDIOCRAdapter` | `AzureDIConfig` |

### `create_quality_scorer(settings?)`

Dispatches on `settings.ocr.quality_scorer`:

| Config Value | Adapter |
|-------------|---------|
| `docling` | `DoclingQualityAdapter` |

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

## Container Class

The `Container` holds **lazily initialized singleton** instances of each adapter:

```python
class Container:
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
            self._primary_ocr = create_ocr_engine(
                self._settings.ocr.primary_engine, self._settings
            )
        return self._primary_ocr

    # ... same pattern for all properties
```

Each property:
1. Checks if the private attribute is `None`
2. If so, calls the corresponding factory function
3. Caches the result in the private attribute
4. Returns the cached instance on subsequent calls

### Container Properties

| Property | Factory | Config Field |
|----------|---------|-------------|
| `primary_ocr` | `create_ocr_engine(settings.ocr.primary_engine)` | `ocr.primary_engine` |
| `secondary_ocr` | `create_ocr_engine(settings.ocr.secondary_engine)` | `ocr.secondary_engine` |
| `quality_scorer` | `create_quality_scorer()` | `ocr.quality_scorer` |
| `llm` | `create_llm_provider()` | `llm.provider` |
| `document_store` | `create_document_store()` | `storage.backend` |
| `notification` | `create_notification_port()` | (always WebSocket) |

### `get_container()`

Returns a cached `Container` singleton:

```python
@lru_cache
def get_container() -> Container:
    return Container()
```

## FastAPI Integration

**File:** `backend/app/api/dependencies.py`

Exposes simple functions that route handlers can use with `Depends()`:

```python
from app.config.container import get_container

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
```

Usage in a route handler:

```python
from fastapi import Depends
from app.api.dependencies import get_primary_ocr
from app.core.ports.ocr import OCREngine

@router.post("/process")
async def process_document(ocr: OCREngine = Depends(get_primary_ocr)):
    result = await ocr.extract(document)
    return result
```

## Swapping an Engine

To add a new adapter (e.g., a new OCR engine called `tesseract`):

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

### 2. Register in the Factory

Add a new case to `create_ocr_engine()` in `container.py`:

```python
def create_ocr_engine(engine_name: str, settings: AppSettings | None = None) -> OCREngine:
    settings = settings or get_settings()
    match engine_name:
        case "marker":
            from app.adapters.ocr.marker import MarkerOCRAdapter
            return MarkerOCRAdapter(settings.marker)
        case "azure_di":
            from app.adapters.ocr.azure_di import AzureDIOCRAdapter
            return AzureDIOCRAdapter(settings.azure_di)
        case "tesseract":
            from app.adapters.ocr.tesseract import TesseractOCRAdapter
            return TesseractOCRAdapter(settings.marker)  # or a new TesseractConfig
        case _:
            raise ValueError(f"Unknown OCR engine: {engine_name}")
```

### 3. Set the Configuration

```yaml
# config/settings.dev.yaml
ocr:
  primary_engine: tesseract
```

Or via environment variable:

```bash
AT_OCR__PRIMARY_ENGINE=tesseract
```

No changes needed in route handlers, the Container, or FastAPI dependencies — the new adapter flows through automatically.

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
