# Pipeline Modes

The system supports three independent processing flows, configured via `pipeline.mode`
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

## Data Lab Mode (`datalab`)

**Best for**: Superior handwriting recognition, better table fidelity, API-based OCR.

```
PDF → Data Lab (Chandra API) → Parallel chunk processing → Confidence fallback → HITL → Store
```

**What runs**:
- Data Lab Convert API (paginated markdown with handwriting, signatures, tables, formulas)
- Data Lab Extract API (structured fields from `extraction_schemas.yaml`)
- Optional JSON bbox enrichment for per-block bounding boxes
- Parallel chunk processing via `asyncio.Semaphore` + `asyncio.gather`

**What does NOT run**:
- Azure Document Intelligence — not needed
- Marker OCR — not needed

**Confidence scoring**:
- No per-word confidence available from Data Lab; falls back to 0.5 baseline
- 100% Validation rules pass rate (when word confidences are missing)
- Optional: fixed 0.85 confidence heuristic for `accurate` mode

**Key advantages**:
- Superior handwriting recognition (cursive, scrawled signatures)
- Better table reconstruction (merged cells, nested tables)
- Checkbox/selection mark detection
- Formula (LaTeX) parsing
- Signature block detection
- 90+ language support

**Latency** (185-page BMR, `mode=accurate`):
- Sequential: ~42 minutes
- Parallel 8×25pp chunks: ~8.7 minutes (4.8× speedup)
- Parallel 4×50pp chunks: ~3.2 minutes (13× speedup, fewer KV pairs)

**Config**:
```yaml
pipeline:
  mode: datalab

datalab:
  api_key: "your-api-key-here"
  mode: accurate                  # fast | balanced | accurate
  chunk_pages: 50
  max_concurrent_chunks: 8
  enable_extraction: true
  extraction_schema_family: bpr_core
```

---

## Comparison

| Feature | azure_di | marker_docling | datalab |
|---------|----------|---------------|---------|
| Cloud dependency | Yes (or disconnected container) | None | Yes (API) |
| Local ML models | None | ~7 GB | None |
| Startup time | Instant | 30-60s (model loading) | Instant |
| Handwriting detection | Native | Limited | Superior |
| Barcode reading | 17+ types | No | No |
| Selection marks | Yes | No | Yes (parsed) |
| Cross-page tables | Native | LLM-powered | Native |
| Per-word confidence | Yes | No | No (0.5 fallback) |
| Table fidelity | Good | Basic (markdown) | Best (merged cells, nested) |
| Formula parsing | Yes | No | Yes (LaTeX) |
| Signature detection | Yes (regions) | No | Yes (block-level) |
| Parallel chunking | No | No | Yes (configurable) |
| Fully air-gapped | With disconnected container | Yes | With self-hosted Chandra |

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
export AT_PIPELINE__MODE=marker_docling  # or azure_di, or datalab
```

No code changes required. The Hexagonal Architecture handles the rest.

---

## Visual Compliance (Cross-Cutting)

Independent of the OCR pipeline mode, the system can run **VLM visual compliance checks** when `vlm.enabled = true`. This adds a vision-language model evaluation pass during compliance analysis, running alongside the text-based evaluator:

```yaml
vlm:
  enabled: true
  provider: gemini      # or vllm for container-hosted
  gemini_api_key: "..."
  gemini_model: gemini-2.5-flash
```

Rules tagged with `evaluation_strategy: vision` or `text_and_vision` will have their pages sent to the VLM for visual analysis (strikethrough detection, signature verification, ink color, etc.). See [VLM Visual Compliance Spec](../specs/vlm-visual-compliance-spec.md) for details.

---

## OCR Correction Learning (Cross-Cutting)

When `feedback.auto_correct_enabled = true`, the system applies learned OCR corrections to new documents based on reviewer edits aggregated across all processed documents. Corrections are managed via the `/api/corrections` API and the `/corrections` frontend page. See [OCR Correction Spec](../specs/ocr-correction-learning-spec.md) for details.
