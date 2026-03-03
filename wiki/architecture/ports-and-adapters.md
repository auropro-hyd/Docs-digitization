# Ports & Adapters Reference

This document covers all five ports in the system, their Protocol definitions, adapter implementations, and how to extend them.

All ports live in `backend/app/core/ports/` and use Python's `typing.Protocol` for structural subtyping -- adapters don't need to inherit from anything, they just need to implement the right methods.

---

## 1. OCREngine Port

**Location:** `app/core/ports/ocr.py`

### Protocol Definition

```python
class OCREngine(Protocol):
    """Port for OCR extraction engines."""

    async def extract(self, pdf_path: str, pages: list[int] | None = None) -> OCRResult:
        """Extract text and structure from a PDF document."""
        ...

    def supports_handwriting(self) -> bool:
        """Whether this engine can detect handwritten content."""
        ...

    def supports_barcodes(self) -> bool:
        """Whether this engine can read barcodes/QR codes."""
        ...

    def supports_selection_marks(self) -> bool:
        """Whether this engine can detect checkboxes/radio buttons."""
        ...
```

### Return Types

`extract()` returns an `OCRResult` containing:

```python
class OCRResult(BaseModel):
    pages: list[OCRPageResult]   # Per-page results
    full_markdown: str            # Complete document markdown
    raw_response: dict | None     # Vendor-specific raw output (excluded from serialization)

class OCRPageResult(BaseModel):
    page_num: int
    markdown: str                          # Page content as markdown
    words: list[OCRWord]                   # Individual words with confidence
    barcodes: list[BarcodeResult]          # Detected barcodes
    selection_marks: list[SelectionMark]   # Checkboxes/radio buttons
    images: dict[str, bytes]               # Extracted images
```

Each `OCRWord` carries a confidence score (0.0-1.0), a handwriting flag, and an optional bounding region.

### Adapters

#### MarkerOCRAdapter (`app/adapters/ocr/marker.py`)

Wraps Marker v1.10+ `PdfConverter` for high-quality PDF-to-Markdown conversion.

| Capability | Supported |
|-----------|-----------|
| Handwriting | Yes (when `use_llm=True`, via Ollama) |
| Barcodes | No |
| Selection Marks | No |
| Per-word confidence | No (Marker produces markdown, not word-level output) |
| Cross-page tables | Yes (`no_merge_tables_across_pages` config) |

Key behaviors:
- **Lazy model loading** -- the Marker model dict is only created on first `extract()` call
- **LLM-powered processors** -- when enabled, uses `OllamaService` for table merging, form extraction, and handwriting recognition
- **Page separation** -- splits output on `\n\n---\n\n` separator when `paginate_output=True`
- **Runs in executor** -- Marker is synchronous; wrapped in `run_in_executor` for async compatibility

#### AzureDIOCRAdapter (`app/adapters/ocr/azure_di.py`)

Uses Azure Document Intelligence `prebuilt-layout` model via the REST API.

| Capability | Supported |
|-----------|-----------|
| Handwriting | Yes (per-word `is_handwritten` flag) |
| Barcodes | Yes (17+ barcode types) |
| Selection Marks | Yes (checkboxes, radio buttons) |
| Per-word confidence | Yes (0.0-1.0 per word) |
| Cross-page tables | Yes (via Azure DI table spans) |

Key behaviors:
- **Same adapter, different endpoint** -- local dev uses Azure AI Foundry cloud API; on-prem production uses the disconnected container
- **Configurable features** -- `features` list in config controls which capabilities are enabled (e.g., `["barcodes", "keyValuePairs"]`)
- **Full bounding regions** -- extracts polygon coordinates for every word, barcode, and selection mark
- **Lazy client creation** -- `DocumentIntelligenceClient` instantiated on first use

### How to Add a New OCR Engine

1. **Create the adapter** in `app/adapters/ocr/your_engine.py`:

```python
from app.core.ports.ocr import OCRPageResult, OCRResult

class YourEngineAdapter:
    def __init__(self, config):
        self._config = config

    async def extract(self, pdf_path: str, pages: list[int] | None = None) -> OCRResult:
        # Your implementation here
        ...

    def supports_handwriting(self) -> bool:
        return False

    def supports_barcodes(self) -> bool:
        return False

    def supports_selection_marks(self) -> bool:
        return False
```

2. **Add a config model** in `app/config/settings.py` if needed:

```python
class YourEngineConfig(BaseModel):
    endpoint: str = "http://localhost:8080"
    # ... your config fields
```

3. **Register in the container** (`app/config/container.py`):

```python
def create_ocr_engine(engine_name: str, settings: AppSettings | None = None) -> OCREngine:
    match engine_name:
        case "marker":
            ...
        case "azure_di":
            ...
        case "your_engine":
            from app.adapters.ocr.your_engine import YourEngineAdapter
            return YourEngineAdapter(settings.your_engine)
```

4. **Set config** in your environment's `settings.*.yaml`:

```yaml
ocr:
  primary_engine: your_engine
```

No other code changes needed -- the workflow, confidence scoring, and HITL routing all work through the `OCREngine` port.

---

## 2. LLMProvider Port

**Location:** `app/core/ports/llm.py`

### Protocol Definition

```python
class LLMProvider(Protocol):
    """Port for LLM inference providers."""

    async def generate(self, prompt: str, *, system: str | None = None) -> str:
        """Generate a text response from a prompt."""
        ...

    async def generate_structured(
        self, prompt: str, schema: type[BaseModel], *, system: str | None = None
    ) -> BaseModel:
        """Generate a structured response conforming to a Pydantic schema."""
        ...
```

### Contract

- `generate()` -- free-form text generation. Accepts an optional system prompt.
- `generate_structured()` -- returns a validated Pydantic model instance. The adapter is responsible for schema injection into the prompt and JSON parsing/validation of the response.

Both methods are `async` and expected to handle retries/timeouts internally.

### Adapters

#### OllamaLLMAdapter (`app/adapters/llm/ollama.py`)

The **production adapter** for on-prem inference.

- Connects to a local or remote Ollama instance via HTTP (`httpx.AsyncClient`)
- Uses the `/api/generate` endpoint with `stream=False`
- For structured output: injects the Pydantic JSON schema into the prompt, parses the response, strips markdown code fences, and validates against the schema
- Default model: `gemma2:9b`
- 120-second timeout for long generation tasks

#### AzureOpenAILLMAdapter (`app/adapters/llm/azure_openai.py`)

The **dev/staging fallback** when Ollama is unavailable or higher-quality inference is needed.

- Uses Azure AI Foundry chat completions endpoint
- API version: `2024-08-01-preview`
- Authentication via `api-key` header
- Low temperature (0.1) for deterministic compliance analysis
- Same structured output strategy: schema in prompt, JSON parsing, Pydantic validation

### Switching Providers

In `settings.*.yaml`:

```yaml
llm:
  provider: ollama          # or "azure_openai"
  base_url: http://localhost:11434
  model: gemma2:9b
  # Azure-specific (only needed for azure_openai):
  azure_endpoint: https://your-resource.openai.azure.com
  azure_deployment: gpt-4o
  api_key: your-key
```

---

## 3. QualityScorer Port

**Location:** `app/core/ports/quality.py`

### Protocol Definition

```python
class QualityScorer(Protocol):
    """Port for document quality scoring engines."""

    async def score(self, pdf_path: str) -> QualityReport:
        """Produce a quality report for the given PDF."""
        ...
```

### Return Type

`QualityReport` provides both document-level and per-page quality metrics:

```python
class QualityReport(BaseModel):
    layout_score: float    # 0.0–1.0 — page layout detection quality
    table_score: float     # 0.0–1.0 — table structure recognition quality
    ocr_score: float       # 0.0–1.0 — text recognition quality
    parse_score: float     # 0.0–1.0 — content parsing quality
    mean_score: float      # 0.0–1.0 — average of all four scores
    low_score: float | None
    per_page: dict[int, PageQualityScore]

class PageQualityScore(BaseModel):
    page_num: int
    layout_score: float
    table_score: float
    ocr_score: float
    parse_score: float

    @property
    def mean_score(self) -> float: ...

    @property
    def grade(self) -> QualityGrade: ...  # EXCELLENT (>=0.9), GOOD (>=0.7), FAIR (>=0.5), POOR
```

Quality scores feed directly into the composite confidence calculation in `app/core/services/confidence.py`, which determines whether a page needs human review.

### Adapter

#### DoclingQualityAdapter (`app/adapters/quality/docling.py`)

The sole quality scoring adapter, using IBM's [Docling](https://github.com/DS4SD/docling) library.

- **MIT licensed**, free, no API keys required
- **CPU-only** -- runs anywhere without GPU
- Uses `DocumentConverter` to process the PDF and extract per-page confidence metrics
- **Lazy initialization** -- converter created on first `score()` call
- Falls back to 0.5 scores across the board if Docling doesn't produce a confidence report

Docling is used **only for quality assessment**, not for extraction content. The actual OCR content comes from Marker and Azure DI.

---

## 4. DocumentStore Port

**Location:** `app/core/ports/storage.py`

### Protocol Definition

```python
class DocumentStore(Protocol):
    """Port for document persistence."""

    async def save_document(self, doc: DigitalDocument) -> str:
        """Save a digitalized document and return its ID."""
        ...

    async def get_document(self, doc_id: str) -> DigitalDocument | None:
        """Retrieve a digitalized document by ID."""
        ...

    async def list_documents(self, *, limit: int = 50, offset: int = 0) -> list[DigitalDocument]:
        """List documents with pagination."""
        ...

    async def save_file(self, file_bytes: bytes, filename: str) -> str:
        """Save a raw file (PDF, image) and return the storage path/URL."""
        ...

    async def get_file(self, path: str) -> bytes:
        """Retrieve a raw file by its storage path."""
        ...
```

### Contract

The port separates two concerns:

1. **Structured documents** (`save_document`/`get_document`/`list_documents`) -- serialized `DigitalDocument` models with metadata, sections, quality reports
2. **Raw files** (`save_file`/`get_file`) -- binary PDF and image storage

This separation lets the implementation choose different backends for each (e.g., PostgreSQL for document metadata + filesystem for raw files).

### Adapters

#### FileSystemAdapter (`app/adapters/storage/filesystem.py`)

Used in **local dev** and **on-prem production**.

- Stores documents as `{base_path}/{doc_id}/document.json`
- Raw files stored directly under `{base_path}/`
- Auto-creates directories as needed
- List operation sorts by modification time (newest first)
- No external dependencies beyond the filesystem

#### AzureBlobAdapter (`app/adapters/storage/azure_blob.py`)

Used in **Azure staging** deployments.

- Connects via `azure_connection_string` to an Azure Blob Storage container
- Currently a placeholder (methods raise `NotImplementedError`) -- to be implemented when staging deployment is configured

### Switching Backends

```yaml
storage:
  backend: filesystem        # or "azure_blob"
  base_path: ./data/documents
  # Azure-specific:
  azure_connection_string: DefaultEndpointsProtocol=https;...
  azure_container: documents
```

---

## 5. NotificationPort

**Location:** `app/core/ports/notification.py`

### Protocol Definition

```python
class NotificationPort(Protocol):
    """Port for real-time notifications to clients."""

    async def send_update(self, channel: str, data: dict) -> None:
        """Push an update to a named channel."""
        ...

    async def subscribe(self, channel: str) -> AsyncIterator[dict]:
        """Subscribe to updates on a named channel."""
        ...
```

### Contract

- **Channels** are identified by `doc_id` -- each document processing run has its own notification channel
- **`send_update`** is fire-and-forget (no delivery guarantee to specific clients)
- **`subscribe`** returns an async iterator for consuming events on a channel
- Events are plain dicts with a `type` field (e.g., `"status"`, `"hitl_required"`, `"error"`)

### Adapters

#### WebSocketNotifyAdapter (`app/adapters/notification/websocket.py`)

The **primary adapter** for single-worker deployments.

- Pushes updates directly to connected WebSocket clients via the FastAPI `ConnectionManager`
- Uses a **push model** -- `send_update` broadcasts to all subscribers on a channel
- `subscribe` is not implemented (clients connect via WebSocket, not pull)
- Zero infrastructure overhead

#### PGListenNotifyAdapter (`app/adapters/notification/pg_notify.py`)

For **multi-worker deployments** where WebSocket connections may be spread across different FastAPI processes.

- Uses PostgreSQL's built-in `LISTEN`/`NOTIFY` mechanism
- `send_update` executes `NOTIFY channel, payload` via `asyncpg`
- `subscribe` calls `add_listener` on the connection and yields events from an `asyncio.Queue`
- Zero additional infrastructure beyond the existing PostgreSQL database
- Each worker `LISTEN`s on relevant channels and forwards to its local WebSocket connections

---

## Port Dependency Map

Shows which workflow nodes and services consume each port:

| Port | Consumed By |
|------|-------------|
| `OCREngine` (primary) | `run_marker_ocr` node |
| `OCREngine` (secondary) | `run_azure_di_ocr` node |
| `QualityScorer` | `run_quality_scoring` node |
| `LLMProvider` | ALCOA, GMP, Checklist, SOP compliance agents |
| `DocumentStore` | `store_results` node, API routes |
| `NotificationPort` | Every workflow node (status updates), HITL review |

---

## Related Pages

- [Architecture Overview](overview.md) -- Why Hexagonal, layer breakdown, system diagram
- [Data Flow](data-flow.md) -- How ports are invoked in the processing pipeline
- [Deployment Environments](deployment-environments.md) -- Which adapters are active per environment
- [Back to Wiki Home](../README.md)
