# OCR Engine Strategy

## Pipeline Modes

The system supports three independent processing flows, selected via `pipeline.mode` config.
Each mode uses a different OCR engine with its own confidence scoring approach.

| Mode | OCR Engine | Quality/Confidence Source | Cloud Dependency |
|------|-----------|-------------------------|-----------------|
| `azure_di` (default) | Azure Document Intelligence | Per-word confidence scores from DI | Cloud API or disconnected container |
| `marker_docling` | Marker + Docling | Docling quality scores per page | None (fully offline) |
| `datalab` | Data Lab (Chandra) | Validation rules (no per-word confidence) | Cloud API (or self-hosted Chandra) |

---

## Engine Capabilities

| Capability | Azure DI | Marker | Docling | Data Lab |
|---|---|---|---|---|
| **PDF → Markdown** | Full content string | Paginated Markdown with structure | Quality report only | Paginated Markdown with rich structure |
| **Handwriting** | Per-word `is_handwritten` flag | Via LLM (when `use_llm: true`) | No | Block-level via `new_block_types` (superior) |
| **Tables** | Native cross-page via `bounding_regions` | LLM-powered cross-page merge | Page-by-page only | Native with merged cells, nested tables |
| **Barcodes** | 17+ symbologies | No | No | No |
| **Selection marks** | Checkbox / radio state + confidence | No | No | Parsed from `☐`/`☑` patterns |
| **Signatures** | Detected regions | No | No | Block-level via `new_block_types` |
| **Formulas** | Yes | No | No | Yes (LaTeX in markdown) |
| **Confidence** | Per-word 0.0–1.0 | Table score 1–5 | layout, table, OCR, parse (0.0–1.0) | Quality score 0–5 (no per-word) |
| **Structured extraction** | KV pairs, query fields | No | No | Extract API with JSON schema |
| **Parallel chunking** | No | No | No | Yes (configurable chunks + concurrency) |
| **License** | Azure pricing / disconnected container | GPL-3.0 (commercial available) | MIT | API pricing / self-hosted Chandra |

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

## datalab Mode

Data Lab (Chandra) handles extraction via its Convert API, with optional structured extraction via the Extract API. Confidence falls back to a 0.5 baseline (no per-word scores available):

```
Confidence = 100% Validation rules pass rate (when word confidences are missing)
```

**Flow:**
```
PDF → Data Lab (chunked, parallel) → merge_azure_di_results (reused) → confidence routing → HITL / auto-approve → store
```

**Strengths:** Superior handwriting OCR, best table fidelity, signature/formula/checkbox detection, parallel chunk processing.
**Requires:** Data Lab API key (or self-hosted Chandra model).

**Latency** (185-page BMR, `mode=accurate`):
- Sequential: ~42 minutes
- Parallel 8×25pp chunks: ~8.7 minutes
- Parallel 4×50pp chunks: ~3.2 minutes

---

## Switching Modes

One config change, no code changes:

```yaml
pipeline:
  mode: azure_di       # or marker_docling, or datalab
```

Or via environment variable:
```bash
AT_PIPELINE__MODE=datalab
```

---

## Related Pages

- [Azure DI adapter details](azure-di.md)
- [Marker adapter details](marker.md)
- [Docling quality adapter details](docling.md)
- [Pipeline modes guide](../../pipeline-modes.md)
- [Data Lab OCR Spec](../../../specs/datalab-ocr-adapter-spec.md)
