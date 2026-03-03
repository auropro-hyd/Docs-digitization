# Marker OCR Adapter

> **Code reference:** `backend/app/adapters/ocr/marker.py`
> **Config model:** `backend/app/config/settings.py` → `MarkerConfig`

## What Marker Does

Marker (v1.10+) converts PDF documents to **paginated Markdown** with high structural fidelity. When LLM mode is enabled it activates **9 LLM-powered processors** that dramatically improve quality for tables, handwriting, forms, equations, and complex layout regions.

In the Auto Transcription pipeline, Marker is the **primary extraction engine** — its Markdown output is the canonical document representation consumed by all downstream stages.

---

## Configuration Reference

All options live in `MarkerConfig` (Pydantic model):

```python
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
```

### Option Details

| Option | Default | Purpose |
|---|---|---|
| `use_llm` | `True` | Master switch for all 9 LLM processors. When `False`, Marker falls back to heuristic-only processing. |
| `paginate_output` | `True` | Insert page separator (`\n\n---\n\n`) between pages so the adapter can split output into per-page results. |
| `extract_images` | `True` | Extract embedded images from the PDF alongside text. |
| `no_merge_tables_across_pages` | `False` | Set to `False` to **enable** cross-page table merging. The double-negative config name is Marker's convention. |
| `table_height_threshold` | `0.6` | Minimum height ratio (relative to page height) for a table to be considered a merge candidate. Tables shorter than 60 % of the page are assumed to be self-contained. |
| `max_table_rows` | `175` | Tables exceeding this row count are chunked before LLM processing to stay within context limits. |
| `html_tables_in_markdown` | `False` | When `True`, tables are rendered as HTML `<table>` elements instead of Markdown pipe tables. |
| `ollama_base_url` | `http://localhost:11434` | Endpoint for the Ollama LLM server used by Marker's LLM processors. |
| `ollama_model` | `gemma2:9b` | Model name passed to OllamaService. |

---

## OllamaService Integration

When `use_llm` is enabled, the converter is initialised with:

```python
kwargs["llm_service"] = "marker.services.ollama.OllamaService"
```

Marker resolves this to its built-in `OllamaService` class which calls the local Ollama server at `ollama_base_url` with `ollama_model`. This keeps all LLM inference **on-prem** — no cloud calls are made.

---

## The 9 LLM Processors

When `use_llm: True`, Marker enables the following processors:

| # | Processor | What it does |
|---|---|---|
| 1 | **Table Correction** | Iteratively scores table Markdown (1–5) and re-generates until the score meets a threshold or max iterations. |
| 2 | **Table Merging** | `LLMTableMergeProcessor` — identifies tables that span a page break and merges them (see below). |
| 3 | **Handwriting OCR** | Detects and transcribes handwritten regions using the LLM's vision capabilities. |
| 4 | **Form Extraction** | Identifies form fields (labels + values) and structures them. |
| 5 | **Complex Region Handling** | Processes regions with mixed content (text + tables + images) that heuristic parsers struggle with. |
| 6 | **Image Description** | Generates alt-text descriptions for embedded images. |
| 7 | **Equation OCR** | Converts mathematical notation to LaTeX. |
| 8 | **Page Correction** | Post-processes each page's Markdown to fix OCR artefacts and formatting issues. |
| 9 | **Section Header Detection** | Identifies and correctly levels section headings (H1–H6). |

### Table Correction — Iterative Scoring

The table correction processor works in a loop:

1. Render the table as Markdown.
2. Ask the LLM to score accuracy from **1** (unusable) to **5** (perfect).
3. If score < threshold → ask the LLM to fix the table and go to step 2.
4. Stop when score ≥ threshold or max iterations reached.

This produces both the corrected table and a **table quality score** (1–5) that feeds into the composite confidence.

---

## Cross-Page Table Merging

The `LLMTableMergeProcessor` handles tables that span page breaks:

1. **Heuristic candidate selection** — A table at the bottom of page *N* and a table at the top of page *N+1* are merge candidates if:
   - The bottom table's lower edge is within `table_height_threshold` (60 %) of the page height.
   - Column counts are compatible.
2. **LLM verification** — The candidate pair is sent to the LLM with the prompt: *"Do these two tables belong together?"*. The LLM returns a boolean.
3. **Merge** — If confirmed, the two Markdown tables are concatenated (header of the second table is dropped) and the result replaces both originals.

This two-stage approach avoids false merges while catching genuine cross-page tables that heuristics alone would miss.

---

## OCRResult Mapping

The adapter splits Marker's single Markdown string into per-page results using the page separator:

```python
PAGE_SEPARATOR = "\n\n---\n\n"

page_markdowns = rendered.split(PAGE_SEPARATOR)
```

Each page becomes an `OCRPageResult`:

| Field | Value |
|---|---|
| `page_num` | 1-indexed position in the split list |
| `markdown` | Stripped page Markdown |
| `words` | Empty list — Marker does not provide word-level data |
| `images` | Empty dict (images are handled separately) |

The complete, un-split Markdown is stored in `OCRResult.full_markdown`.

### Capability Flags

```python
supports_handwriting()    → True  (when use_llm is True)
supports_barcodes()       → False
supports_selection_marks() → False
```

---

## Performance Considerations

### Lazy Model Loading

The `PdfConverter` and its model artifacts are heavy (~2 GB). The adapter uses **lazy initialisation** — `_get_converter()` is called on the first `extract()` invocation, not at construction time. Subsequent calls reuse the cached converter.

```python
def _get_converter(self):
    if self._converter is not None:
        return self._converter
    # ... heavy init ...
    self._converter = PdfConverter(**kwargs)
    return self._converter
```

### `run_in_executor` for Sync Code

Marker's `PdfConverter.__call__` is synchronous and CPU-intensive. The adapter wraps it with `run_in_executor` to avoid blocking the async event loop:

```python
async def extract(self, pdf_path, pages=None):
    converter = self._get_converter()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, converter, pdf_path)
```

This delegates the work to the default `ThreadPoolExecutor`, keeping the event loop responsive for concurrent Azure DI and Docling calls.

---

## Related Pages

- [OCR engine overview](overview.md)
- [Azure DI adapter](azure-di.md)
- [Docling quality adapter](docling.md)
