# Pipeline Modes

The system supports two independent processing flows, configured via `pipeline.mode`
in your settings YAML or the `AT_PIPELINE__MODE` environment variable.

---

## Azure DI Mode (`azure_di`) — Default

**Best for**: Fast setup, high accuracy, minimal infrastructure.

```
PDF → Azure DI (cloud or container) → Confidence from word-level scores → HITL → Store
```

**What runs**:
- Azure Document Intelligence (prebuilt-layout model)
- Validation rules (date plausibility, content length, etc.)

**What does NOT run**:
- Marker OCR — not needed
- Docling — not needed
- Ollama — not needed (unless using compliance agents)

**Confidence scoring**:
- 50% Azure DI average word confidence
- 20% Azure DI minimum word confidence
- 30% Validation rules pass rate

**Config**:
```yaml
pipeline:
  mode: azure_di

azure_di:
  endpoint: "https://your-resource.cognitiveservices.azure.com"  # or http://localhost:5000
  api_key: "your-key"
```

---

## Marker + Docling Mode (`marker_docling`)

**Best for**: Fully offline, zero cloud dependency, air-gapped environments.

```
PDF → Marker OCR → Docling Quality Scoring → Confidence from quality scores → HITL → Store
```

**What runs**:
- Marker OCR (PDF → Markdown with tables, images, cross-page merging)
- Docling (quality scoring: layout, table, OCR, parse scores)
- Ollama (local LLM for Marker's table merging + compliance agents)

**What does NOT run**:
- Azure Document Intelligence — not needed

**Confidence scoring**:
- 60% Docling quality mean (average of layout, table, OCR, parse scores)
- 40% Validation rules pass rate

**Prerequisites**:
```bash
# Start Ollama with required model (~7 GB download on first run)
ollama pull gemma2:9b
ollama serve
```

**Config**:
```yaml
pipeline:
  mode: marker_docling

marker:
  use_llm: true
  ollama_base_url: "http://localhost:11434"
  ollama_model: "gemma2:9b"
```

---

## Comparison

| Feature | azure_di | marker_docling |
|---------|----------|---------------|
| Cloud dependency | Yes (or disconnected container) | None |
| Local ML models | None | ~7 GB |
| Startup time | Instant | 30-60s (model loading) |
| Handwriting detection | Native | Limited |
| Barcode reading | 17+ types | No |
| Selection marks | Yes | No |
| Cross-page tables | Native | LLM-powered |
| Per-word confidence | Yes | No |
| Fully air-gapped | With disconnected container | Yes |

---

## Switching Modes

Change one line in your config:

```yaml
# Switch to offline mode
pipeline:
  mode: marker_docling

# Switch back to Azure DI
pipeline:
  mode: azure_di
```

Or via environment variable:
```bash
export AT_PIPELINE__MODE=marker_docling  # or azure_di
```

No code changes required. The Hexagonal Architecture handles the rest.
