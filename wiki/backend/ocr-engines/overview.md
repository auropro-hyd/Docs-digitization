# OCR Engine Strategy

## Pipeline Modes

The system supports two independent processing flows, selected via `pipeline.mode` config.
Each mode uses a different OCR engine with its own confidence scoring approach.

| Mode | OCR Engine | Quality/Confidence Source | Cloud Dependency |
|------|-----------|-------------------------|-----------------|
| `azure_di` (default) | Azure Document Intelligence | Per-word confidence scores from DI | Cloud API or disconnected container |
| `marker_docling` | Marker + Docling | Docling quality scores per page | None (fully offline) |

---

## Engine Capabilities

| Capability | Azure DI | Marker | Docling |
|---|---|---|---|
| **PDF → Markdown** | Full content string | Paginated Markdown with structure | Quality report only |
| **Handwriting** | Per-word `is_handwritten` flag | Via LLM (when `use_llm: true`) | No |
| **Tables** | Native cross-page via `bounding_regions` | LLM-powered cross-page merge | Page-by-page only |
| **Barcodes** | 17+ symbologies | No | No |
| **Selection marks** | Checkbox / radio state + confidence | No | No |
| **Confidence** | Per-word 0.0–1.0 | Table score 1–5 | layout, table, OCR, parse (0.0–1.0) |
| **License** | Azure pricing / disconnected container | GPL-3.0 (commercial available) | MIT |

---

## azure_di Mode (Default)

Azure Document Intelligence handles the full extraction. Confidence scoring uses
DI's per-word scores combined with validation rules:

```
Confidence = 0.50 × avg_word_confidence + 0.20 × min_word_confidence + 0.30 × validation_pass_rate
```

**Flow:**
```
PDF → Azure DI → merge_azure_di_results → confidence routing → HITL / auto-approve → store
```

**Strengths:** Fast, no local ML, native handwriting/barcode/selection-mark support.
**Requires:** Azure DI endpoint (cloud API or disconnected container).

---

## marker_docling Mode

Marker OCR extracts content; Docling provides independent quality scores.
Confidence uses Docling's per-page quality dimensions:

```
Confidence = 0.60 × docling_quality_mean + 0.40 × validation_pass_rate
```

**Flow:**
```
PDF → Marker OCR → Docling quality scoring → merge_marker_results → confidence routing → HITL / auto-approve → store
```

**Strengths:** Fully offline, zero cloud dependency, strong LLM-powered table handling.
**Requires:** Ollama (gemma2:9b, ~7 GB), Marker, Docling.

---

## Switching Modes

One config change, no code changes:

```yaml
pipeline:
  mode: azure_di       # or marker_docling
```

Or via environment variable:
```bash
AT_PIPELINE__MODE=marker_docling
```

---

## Related Pages

- [Azure DI adapter details](azure-di.md)
- [Marker adapter details](marker.md)
- [Docling quality adapter details](docling.md)
- [Pipeline modes guide](../../pipeline-modes.md)
