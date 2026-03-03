# Docling Quality Scoring Adapter

> **Code reference:** `backend/app/adapters/quality/docling.py`
> **Quality models:** `backend/app/core/models/quality.py`

## Purpose

Docling is used **exclusively for quality scoring**. It does not contribute any extraction content (Markdown, words, barcodes, etc.) to the pipeline output. Its sole job is to produce a `QualityReport` that quantifies how well the document was processed across four independent dimensions.

This separation of concerns allows Docling to run as a fully independent quality gate — its scores can trigger human review without affecting the extraction path.

---

## About Docling

- **Author:** IBM Research
- **License:** MIT — completely free for commercial and on-prem use
- **Runtime:** CPU-only, no GPU required
- **Package:** `docling` on PyPI

---

## Quality Dimensions

Docling produces four scores per page and four aggregated scores at the document level, each in the range **0.0–1.0**:

| Score | What it measures |
|---|---|
| `layout_score` | Accuracy of page layout detection (text blocks, figures, headers, footers) |
| `table_score` | Quality of table structure recognition (rows, columns, merged cells) |
| `ocr_score` | Character-level OCR recognition confidence |
| `parse_score` | Overall document parsing fidelity (reading order, section hierarchy) |

A `mean_score` is computed as the arithmetic mean of the four dimensions:

```python
mean_score = (layout_score + table_score + ocr_score + parse_score) / 4.0
```

---

## Quality Grades

Each page and the overall document receive a `QualityGrade` based on `mean_score`:

| Grade | Mean score range |
|---|---|
| `EXCELLENT` | ≥ 0.9 |
| `GOOD` | ≥ 0.7 |
| `FAIR` | ≥ 0.5 |
| `POOR` | < 0.5 |

Pages graded `POOR` or `FAIR` are typically flagged for human review.

---

## Data Models

### `QualityReport` (document-level)

```python
class QualityReport(BaseModel):
    layout_score: float    # 0.0–1.0
    table_score: float     # 0.0–1.0
    ocr_score: float       # 0.0–1.0
    parse_score: float     # 0.0–1.0
    mean_score: float      # 0.0–1.0
    low_score: float | None
    per_page: dict[int, PageQualityScore]
```

`low_score` captures the lowest individual score across all dimensions and pages — a quick flag for worst-case quality.

### `PageQualityScore` (per-page)

```python
class PageQualityScore(BaseModel):
    page_num: int
    layout_score: float    # 0.0–1.0
    table_score: float     # 0.0–1.0
    ocr_score: float       # 0.0–1.0
    parse_score: float     # 0.0–1.0
```

---

## Adapter Implementation

### Lazy Converter Initialisation

Like the other adapters, Docling's `DocumentConverter` is lazily loaded on first use:

```python
def _get_converter(self):
    if self._converter is not None:
        return self._converter

    from docling.document_converter import DocumentConverter
    self._converter = DocumentConverter()
    return self._converter
```

### Async Execution

The synchronous `converter.convert()` call is offloaded via `run_in_executor`:

```python
async def score(self, pdf_path: str) -> QualityReport:
    converter = self._get_converter()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, converter.convert, pdf_path)
```

### Graceful Fallback

If Docling does not produce a confidence report (e.g. unsupported format, internal error), the adapter returns neutral mid-point scores rather than failing:

```python
if confidence is None:
    return QualityReport(
        layout_score=0.5,
        table_score=0.5,
        ocr_score=0.5,
        parse_score=0.5,
        mean_score=0.5,
    )
```

This ensures the pipeline never blocks on a quality-scoring failure.

---

## Parallel Execution

Docling has **no dependency** on Marker or Azure DI results. In the LangGraph orchestration, all three engines are dispatched concurrently via `Send` nodes:

```text
┌────────────┐  ┌──────────┐  ┌─────────┐
│   Marker   │  │ Azure DI │  │ Docling │
│ (extract)  │  │(extract) │  │ (score) │
└─────┬──────┘  └────┬─────┘  └────┬────┘
      └───────────────┼────────────┘
                      ▼
             Merge / Compose
```

Because Docling is CPU-only and runs in a thread pool, it does not compete with Marker for GPU resources (if a GPU is present for Marker's model inference).

---

## How Scores Feed Into Composite Confidence

Docling scores are combined with Marker's table scores and Azure DI's per-word confidence to build the composite confidence report:

| Source | Metric | Scope |
|---|---|---|
| **Docling** | `layout_score`, `table_score`, `ocr_score`, `parse_score` | Per-page + document |
| Marker | Table quality score (1–5) | Per-table |
| Azure DI | Per-word confidence (0.0–1.0) | Per-word |

The composite report exposes all three perspectives so consumers can decide which dimension matters most for their use-case.

---

## CPU Thread Configuration

Docling runs CPU-only. The number of threads used by its underlying models (e.g. ONNX Runtime, PyTorch CPU) can be controlled via standard environment variables:

```bash
OMP_NUM_THREADS=4
MKL_NUM_THREADS=4
```

Adjusting these prevents Docling from saturating all cores when running alongside Marker and Azure DI in the same process.

---

## Related Pages

- [OCR engine overview](overview.md)
- [Marker adapter](marker.md)
- [Azure DI adapter](azure-di.md)
