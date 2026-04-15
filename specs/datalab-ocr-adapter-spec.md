# Data Lab (Chandra) OCR Adapter — Technical Specification

**Status:** Implemented (Phase 1-2 complete, latency optimized) | Phase 3 deferred  
**Author:** Auto-generated  
**Date:** 2026-04-14 | **Updated:** 2026-04-15  
**Depends on:** Existing `OCREngine` port (`backend/app/core/ports/ocr.py`)

---

## 1. Motivation

Azure Document Intelligence is the primary OCR engine. However, Akhilesh's review identified that **handwritten content detection is often inaccurate** compared to Data Lab's Chandra model. Data Lab (datalab.to) offers:

- Superior handwriting recognition (cursive, scrawled signatures, dates)
- Better table reconstruction (colspan/rowspan, nested tables)
- Form/checkbox detection
- 90+ language support
- Chart/diagram understanding

A reference implementation exists at `context/poc/infra/ChandraExtraction.py` using `datalab-python-sdk`. The goal is to build a production-grade adapter that plugs into the existing `OCREngine` port.

---

## 2. Data Lab Capabilities vs OCR Port Requirements

### 2.1 Capability Matrix

| OCR Port Requirement | Azure DI | Marker | Data Lab (Chandra) | Notes |
|---------------------|----------|--------|-------------------|-------|
| Per-page markdown | Yes | Yes | Yes (paginate=True) | Split on `"\n\n---\n\n"` delimiter |
| Full document markdown | Yes | Yes | Yes | Direct from SDK |
| Per-word confidence | Yes (0-1) | No | No | Gap — leave empty; merge uses 0.5 baseline |
| Handwriting detection | Yes (per-word) | Conditional (`use_llm`) | Yes (block-level via `new_block_types`) | Different granularity |
| Barcodes | Yes (17+ types) | No | No | Gap — not supported |
| Selection marks / checkboxes | Yes (state + confidence) | No | Yes (in markdown/HTML) | Needs parsing from output |
| Tables with structure | Yes (row/col counts, spans) | Markdown only | Yes (complex tables, `table_row_bboxes`) | Stronger than Marker |
| Key-value pairs | Yes (structured) | No | No (direct); via Extraction API | Gap for direct convert |
| Signatures | Yes (detected regions) | No | Yes (via `new_block_types`) | Block-level, not bbox |
| Formulas (LaTeX) | Yes | No | Yes | Natively in markdown |
| Language detection | Yes (per-span) | No | No (structured) | Gap |
| Style/font spans | Yes | No | No | Gap |
| Images extraction | Yes | Yes | Yes (base64) | Parity |
| Quality score | Per-word confidence | No | `parse_quality_score` (0-5) | Different scale |
| Progress callback | Yes (poller %) | No | Yes (poll-based) | Mappable |

### 2.2 Key Advantages over Azure DI

1. **Handwriting accuracy:** Chandra is purpose-built for handwritten forms; Azure DI struggles with scrawled BMR signatures
2. **Table fidelity:** Better handling of merged cells, nested tables, cross-page continuations
3. **Cost:** Potentially lower per-page cost (~$2-6/1K pages by mode vs Azure DI pricing)
4. **Self-hostable:** Chandra model available on HuggingFace for on-premise deployment

### 2.3 Known Gaps (vs Azure DI)

1. **No per-word OCR confidence** — cannot drive HITL confidence scoring the same way
2. **No structured barcode extraction** — 17+ barcode types in Azure DI not available
3. **No structured KV pair extraction** from convert API — would need separate Extraction API call
4. **No language/style spans** — limited metadata for advanced compliance checks
5. **No query fields** — Azure DI's custom query-based extraction not available

---

## 3. Architecture

### 3.1 New Components

```
backend/
├── app/
│   ├── adapters/
│   │   └── ocr/
│   │       └── datalab.py          # NEW: DatalabOCRAdapter
│   ├── config/
│   │   └── settings.py             # MODIFY: Add DatalabConfig
│   │   └── container.py            # MODIFY: Add "datalab" pipeline mode
│   └── workflow/
│       └── document_graph.py       # MODIFY: Add routing for "datalab" mode
│       └── nodes.py                # NEW: run_datalab_ocr node (or reuse azure path)
```

### 3.2 Pipeline Mode

New `pipeline.mode` value: `"datalab"`

The Data Lab adapter produces an `OCRResult` that flows through the **same merge path** as Azure DI (reusing `merge_azure_di_results` with graceful handling of missing fields), avoiding the need for a separate merge node.

### 3.3 Adapter Class Design

```python
class DatalabOCRAdapter:
    """Data Lab (Chandra) OCR adapter implementing the OCREngine protocol."""

    def __init__(self, config: DatalabConfig) -> None: ...

    async def extract(
        self,
        pdf_path: str,
        pages: list[int] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> OCRResult: ...

    def supports_handwriting(self) -> bool:
        return True

    def supports_barcodes(self) -> bool:
        return False

    def supports_selection_marks(self) -> bool:
        return True  # Parsed from HTML/markdown output
```

---

## 4. Configuration

### 4.1 DatalabConfig (Pydantic settings)

```python
class DatalabConfig(BaseModel):
    """Data Lab OCR settings."""
    api_key: str = ""
    base_url: str = "https://www.datalab.to"
    timeout: int = 300
    mode: str = "accurate"                    # fast | balanced | accurate
    paginate: bool = True
    max_pages: int | None = None
    extras: str = "new_block_types,table_row_bboxes,chart_understanding"
    output_format: str = "markdown"
    disable_image_extraction: bool = True     # Skip image model (saves server time)
    disable_image_captions: bool = True       # Skip caption model (saves server time)
    token_efficient_markdown: bool = False
    page_range: str | None = None
    max_polls: int = 300
    poll_interval: float = 1.0               # SDK default; was 2.0
    chunk_pages: int = 50
    max_concurrent_chunks: int = 8           # API allows 400; 8 is safe default
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

    # Bounding box enrichment via JSON output
    fetch_block_bboxes: bool = True
```

### 4.2 Environment Variables

```bash
AT_PIPELINE__MODE=datalab

AT_DATALAB__API_KEY=your-api-key-here
AT_DATALAB__BASE_URL=https://www.datalab.to
AT_DATALAB__TIMEOUT=300
AT_DATALAB__MODE=accurate
# AT_DATALAB__EXTRAS=["new_block_types","table_row_bboxes","chart_understanding"]
# AT_DATALAB__CHUNK_PAGES=50
```

---

## 5. Adapter Implementation Detail

### 5.1 Core Flow

```
extract(pdf_path, pages, progress_callback)
  │
  ├─ Count total pages (pypdfium2)
  ├─ Build page ranges (chunk if > chunk_pages)
  │
  ├─ asyncio.gather(*chunks, semaphore=max_concurrent_chunks):
  │   └─ _process_single_chunk(chunk_idx, page_range):
  │       ├─ async with semaphore:
  │       ├─ _submit_with_retry(file_path, page_range)
  │       │   └─ AsyncDatalabClient.convert() with exponential backoff
  │       ├─ asyncio.gather(_run_extraction, _fetch_json_bboxes)  # parallel
  │       └─ _process_result(result) → (pages, md, tables, sigs, kv_pairs)
  │
  ├─ Merge chunks (sort pages by page_num, sort markdown by chunk_idx, join)
  └─ Return OCRResult
```

**Performance:** 185-page BMR with `mode=accurate`, `use_llm=true`:
- Sequential (before): 42 minutes
- Parallel 8x25pp (after): 8.7 minutes
- Parallel 4x50pp (after): 3.2 minutes (fewer KV pairs due to larger Extract API context)

### 5.2 Mapping Data Lab Output → OCRResult

| Data Lab field | OCRResult field | Mapping logic |
|---------------|----------------|---------------|
| `result.markdown` | `full_markdown` | Direct; apply `sanitize_layout_markdown` |
| Split on `"\n\n---\n\n"` per page | `pages[].markdown` | Same delimiter as MarkerOCRAdapter (`PAGE_SEPARATOR`) |
| `result.images` | `pages[].images` | Decode base64 → bytes, map by page |
| `result.metadata` + `parse_quality_score` | `raw_response` | Stash for quality dashboard |
| `new_block_types` blocks | `pages[].words` (partial) | Parse handwriting blocks → OCRWord with `is_handwritten=True` |
| Checkboxes in output | `pages[].selection_marks` | Parse `☐`/`☑`/`✓` patterns → SelectionMark |
| Table structure | `table_metadata` | Parse `<table>` tags for row/col counts |
| N/A | `key_value_pairs` | Empty list (or optional 2nd API call to Extraction API) |
| N/A | `styles`, `languages` | Empty lists |
| Signature blocks | `signatures` | Parse from `new_block_types` → SignatureRegion |
| LaTeX in markdown | `pages[].formulas` | Parse `$...$` / `$$...$$` → FormulaResult |
| N/A | `barcodes` | Empty list |

### 5.3 Handwriting Detection

With `extras=["new_block_types"]`, Chandra annotates handwritten regions in the output. The adapter should:

1. Parse handwriting block annotations from the result
2. Create `OCRWord` entries with `is_handwritten=True` for handwritten text
3. Set the page's `handwritten_count` in the extraction metadata

### 5.4 Selection Mark Detection

Chandra reconstructs checkboxes in markdown/HTML. The adapter should:

1. Search for checkbox patterns: `☐`, `☑`, `✓`, `✗`, `[ ]`, `[x]`, `[X]`
2. Create `SelectionMark` entries with `state="selected"/"unselected"` and `confidence=0.9`
3. Provide these for compliance evaluator's selection semantics

### 5.5 Page Range Handling

Data Lab uses **0-based** page ranges vs Azure DI's 1-based:

```python
# Convert 1-based pages list to Data Lab's 0-based format
if pages:
    page_range = ",".join(str(p - 1) for p in pages)
```

### 5.6 Progress Callback

Map SDK polling to the progress callback interface:

```python
# Approximate progress from poll count
percent = min(95, int(poll_count / max_polls * 100))
progress_callback(percent, f"Data Lab processing ({percent}%)")
```

---

## 6. Workflow Integration

### 6.1 Container Wiring

Add to `Container.ocr_engine` property:

```python
case "datalab":
    from app.adapters.ocr.datalab import DatalabOCRAdapter
    self._ocr_engine = DatalabOCRAdapter(self._settings.datalab)
```

### 6.2 Graph Routing

Today, `route_after_ingest` returns `"run_azure_di_ocr"` for **any** mode that isn't `"marker_docling"`. This means `mode="datalab"` already routes to the Azure DI node **without any graph code change** — only the container's `match` statement needs the new `case "datalab":`.

Option A (recommended): Route `"datalab"` to the same `run_azure_di_ocr` node, which already calls `container.ocr_engine.extract()` generically. The merge node (`merge_azure_di_results`) handles empty fields gracefully — empty `word_confidences` falls back to a 0.5 confidence baseline; empty `barcodes`, `selection_marks`, `key_value_pairs`, `styles`, `signatures`, `languages` are all handled via `.get(key, [])`.

**Caveats when reusing the Azure node:**
- Quality gate always reads `settings.azure_di.quality_gate_*` — acceptable initially (Data Lab docs still use same PDF input quality requirements), but may need its own config section later.
- WebSocket progress status says `"azure_di_running"` — cosmetic, not functional.
- `extract_query_fields` / `extract_custom_model` are behind `hasattr()` checks in merge, so they are safely skipped for adapters that don't implement them.

Option B: Create a dedicated `run_datalab_ocr` node if Data Lab needs substantially different post-processing (e.g., different quality gate policy or progress reporting).

### 6.3 Merge Compatibility

`merge_azure_di_results` reads from `azure_di_results` in state. For Data Lab mode:
- The OCR node should populate the same state keys (`azure_di_results`, `raw_markdown`, etc.)
- Fields that Data Lab doesn't provide (word confidences, barcodes) will be empty arrays/dicts
- Downstream merge should handle `len(word_confidences) == 0` gracefully (already does for avg/min with fallback)

---

## 7. Quality & Confidence Mapping

### 7.1 Document Quality

Data Lab provides `parse_quality_score` (0-5 scale). Map to the 0-1 scale used internally:

```python
quality_0_1 = parse_quality_score / 5.0
```

### 7.2 Per-Page Confidence

Without per-word confidence scores, the `merge_azure_di_results` node already handles this: when `word_confidences` is empty, it falls back to a **0.5 baseline** for `avg_confidence` and `min_confidence`. This is conservative but functional.

For better confidence estimation, the adapter can optionally populate `OCRWord` entries with a fixed confidence (e.g., 0.85 for "accurate" mode) to provide the downstream pipeline with non-default values. This is a Phase 2 enhancement.

---

## 8. Hybrid Mode (Future)

For documents that need both strong handwriting OCR (Data Lab) and structured form extraction (Azure DI), a hybrid mode could:

1. Run Data Lab first for markdown + handwriting
2. Run Azure DI for KV pairs, barcodes, selection marks only
3. Merge: prefer Data Lab markdown, augment with Azure DI structured metadata

This is **out of scope** for Phase 1 but should be considered in the architecture.

---

## 9. Dependencies

Add to `backend/pyproject.toml`:

```toml
"datalab-python-sdk>=0.2.0",
```

---

## 10. Implementation Phases

### Phase 1: Core Adapter (COMPLETED)
- [x] `DatalabConfig` in settings.py — 28 fields including `max_concurrent_chunks`, `disable_image_captions`, `poll_interval`
- [x] `DatalabOCRAdapter` implementing `OCREngine` protocol — 829 lines
- [x] Container wiring for `"datalab"` pipeline mode
- [x] Graph routing (reuses `run_azure_di_ocr` path via generic `container.ocr_engine`)
- [x] Chunking + retry for large documents — exponential backoff, configurable retries
- [x] Basic markdown + images extraction
- [x] `.env.example` documentation
- [x] Parallel chunk processing via `asyncio.Semaphore` + `asyncio.gather` (8 concurrent)
- [x] Extraction API integration with `page_schema` from `extraction_schemas.yaml` (5 families)
- [x] Attestation enrichment (`Done By` / `Checked By` -> boolean pairs)
- [x] Critical step detection (quantities, inspection keywords, bold)
- [x] `disable_image_extraction` and `disable_image_captions` for server-side speedup
- [x] `poll_interval` tuned to 1.0s (matching SDK default)

### Phase 2: Enhanced Extraction (COMPLETED)
- [x] Handwriting block detection from `new_block_types` — `<!-- block_type: Handwriting -->` parsing
- [x] Checkbox/selection mark parsing — `☐`, `☑`, `✓`, `[x]`, `[ ]` patterns
- [x] Signature detection — both `<!-- block_type: Signature -->` and `[Signature]` inline text
- [x] Table metadata extraction from pipe-delimited markdown
- [x] Formula parsing — `$...$` inline and `$$...$$` display math
- [x] JSON bbox enrichment — optional second `convert` call with `output_format="json"` for per-block polygons
- [x] Layout markdown sanitization via shared `sanitize_layout_markdown`
- [x] Quality score stored in `raw_response` (`parse_quality_score` / 5.0)

### Latency Optimization (COMPLETED — 2026-04-15)
- [x] Sequential chunk processing replaced with `asyncio.gather` — all chunks in parallel
- [x] 185-page BMR: **42 min -> 8.7 min** (4.8x speedup) with 25-page chunks
- [x] 185-page BMR: **42 min -> 3.2 min** (13x speedup) with 50-page chunks (fewer KV pairs)
- [x] Configurable `max_concurrent_chunks` (default 8, API allows 400 concurrent)
- [x] Image extraction/captions disabled (server-side model skip, zero accuracy impact)
- [x] `result.json` write moved to `asyncio.to_thread` (non-blocking event loop)

### Phase 3: Hybrid Mode (DEFERRED)
- [ ] Combined Data Lab + Azure DI pipeline
- [ ] Configurable per-feature routing (which engine handles what)
- [ ] Unified merge with best-of-both metadata

### Not Yet Implemented
- [ ] Pytest unit/integration tests for `DatalabOCRAdapter`
- [ ] Provider-agnostic node naming (currently `run_azure_di_ocr` / `merge_azure_di_results`)
- [ ] Data Lab-specific quality gate configuration (currently uses Azure DI quality gate settings)

---

## 11. Testing Strategy

| Test | Description |
|------|-------------|
| Unit: adapter init | Verify config wiring, client creation |
| Unit: page splitting | Test markdown split on `---` delimiter |
| Unit: checkbox parsing | Test `☐`/`☑` patterns → SelectionMark |
| Unit: handwriting parsing | Test block annotation → OCRWord mapping |
| Integration: full extract | End-to-end with sample BMR PDF |
| Comparison: DL vs Azure DI | Side-by-side quality on 5 BMR pages with heavy handwriting |
| Merge compatibility | Verify `merge_azure_di_results` handles empty fields |

---

## 12. Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Data Lab API availability | Pipeline blocked if service down | Retry + fallback to Azure DI |
| No per-word confidence | Confidence scoring degraded | Heuristic confidence from quality score |
| Missing KV pairs | Compliance rules using structured fields affected | Fallback to markdown parsing |
| SDK breaking changes | Adapter breaks on upgrade | Pin SDK version, integration tests |
| Cost at scale | 185-page docs may be expensive | Chunk + mode selection (fast for simple pages) |
