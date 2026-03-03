# Multi-Engine OCR Strategy

## Why Multiple Engines?

No single OCR engine excels at every document processing task. Handwriting detection, barcode reading, cross-page table reconstruction, and quality scoring each have a clear best-in-class tool — but that tool is never the same one. The Auto Transcription pipeline therefore runs **three engines in parallel** and composites their outputs to maximise accuracy, coverage, and confidence.

| Concern | Best engine |
|---|---|
| PDF → Markdown (structure preservation) | **Marker** |
| Handwriting detection (per-word flag) | **Azure DI** |
| Barcode / QR-code reading | **Azure DI** |
| Selection marks (checkboxes, radio buttons) | **Azure DI** |
| Cross-page table merging with LLM verification | **Marker** |
| Quality scoring (layout / table / OCR / parse) | **Docling** |

---

## Engine Roles

### Marker — Primary Extraction Engine

Marker converts the PDF into paginated Markdown using 9 LLM-powered processors. It produces the **canonical Markdown** that downstream stages (field extraction, LLM analysis) consume. Its table handling is the strongest of the three engines — including cross-page merge via `LLMTableMergeProcessor` — and it provides an iterative table quality score (1–5).

See [marker.md](marker.md) for full details.

### Azure Document Intelligence — Supplementary Extraction Engine

Azure DI enriches the extraction with per-word metadata that Marker cannot provide: `is_handwritten` flags, per-word `confidence` scores, barcode decoding (17+ symbologies), and selection-mark detection. In the composite result the pipeline merges Azure DI word-level data with Marker's structural Markdown.

Two deployment modes are supported with the **same adapter class**:

| Mode | Use-case | Endpoint |
|---|---|---|
| Cloud API (Azure AI Foundry) | Dev / staging | `https://<resource>.cognitiveservices.azure.com` |
| Disconnected container | Production on-prem | `http://localhost:<port>` |

See [azure-di.md](azure-di.md) for full details.

### Docling — Quality Scoring Engine

Docling (MIT, by IBM) does **not** contribute extraction content. It runs a separate analysis pass and produces four per-page quality scores: `layout_score`, `table_score`, `ocr_score`, and `parse_score`. These scores feed into the composite confidence report and help the pipeline flag pages that need human review.

See [docling.md](docling.md) for full details.

---

## Parallel Execution via LangGraph `Send`

All three engines run concurrently. The LangGraph orchestration graph dispatches each engine via `Send` nodes:

```text
                ┌──────────────┐
                │  Load PDF    │
                └──────┬───────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
   ┌────────────┐ ┌──────────┐ ┌─────────┐
   │   Marker   │ │ Azure DI │ │ Docling │
   └─────┬──────┘ └────┬─────┘ └────┬────┘
         │             │             │
         └─────────────┼─────────────┘
                       ▼
              ┌─────────────────┐
              │ Merge / Compose │
              └────────┬────────┘
                       ▼
              ┌─────────────────┐
              │ Composite Result│
              └─────────────────┘
```

Because none of the three engines depends on another's output, the wall-clock time is roughly `max(marker_time, azure_di_time, docling_time)` rather than the sum.

---

## Composite Confidence Score

The merge stage builds a **per-page** and **document-level** composite confidence from all three sources:

| Source | Metric | Range | What it measures |
|---|---|---|---|
| Marker | Table quality score | 1–5 (integer) | LLM-assessed table accuracy after iterative correction |
| Azure DI | Per-word confidence | 0.0–1.0 | OCR recognition certainty for each word |
| Docling | `layout_score` | 0.0–1.0 | Page layout detection quality |
| Docling | `table_score` | 0.0–1.0 | Table structure detection quality |
| Docling | `ocr_score` | 0.0–1.0 | Character recognition quality |
| Docling | `parse_score` | 0.0–1.0 | Document parsing fidelity |

Docling scores are graded per page and per document:

| Grade | Mean score range |
|---|---|
| EXCELLENT | ≥ 0.9 |
| GOOD | ≥ 0.7 |
| FAIR | ≥ 0.5 |
| POOR | < 0.5 |

---

## Engine Comparison Table

| Capability | Marker | Azure DI | Docling |
|---|---|---|---|
| **Handwriting** | Via LLM (when `use_llm: True`) | Per-word `is_handwritten` flag | No |
| **Tables** | Excellent — cross-page merge via LLM | Good — native cross-page via `bounding_regions` | Page-by-page only |
| **Barcodes** | No | 17+ symbologies (Code 128, QR, EAN-13, …) | No |
| **Selection marks** | No | Checkbox / radio state + confidence | No |
| **Confidence metric** | Table score 1–5 | Per-word 0.0–1.0 | `layout_score`, `table_score`, `ocr_score`, `parse_score` (0.0–1.0 each) |
| **Output format** | Paginated Markdown | Per-word JSON + full content string | Quality report only |
| **License** | GPL-3.0 (commercial license available for on-prem) | Azure pricing / disconnected container license | MIT — free |

---

## Related Pages

- [Marker adapter details](marker.md)
- [Azure DI adapter details](azure-di.md)
- [Docling quality adapter details](docling.md)
