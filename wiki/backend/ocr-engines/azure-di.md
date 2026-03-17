# Azure Document Intelligence Adapter

> **Code reference:** `backend/app/adapters/ocr/azure_di.py`
> **Config model:** `backend/app/config/settings.py` → `AzureDIConfig`

## What Azure DI Provides

Azure Document Intelligence (formerly Form Recognizer) is the **default OCR engine** in `azure_di` pipeline mode. It handles the full extraction pipeline — PDF to text with per-word metadata. Key capabilities:

| Capability | Detail |
|---|---|
| **Per-word handwriting detection** | Every word carries an `is_handwritten` boolean |
| **Per-word confidence scores** | Float 0.0–1.0 indicating OCR certainty |
| **Barcode / QR-code reading** | 17+ symbologies decoded per page |
| **Selection mark detection** | Checkbox and radio-button state with confidence |

---

## Deployment Modes

The same `AzureDIOCRAdapter` class is used in all environments. Only the endpoint URL changes:

| Mode | Environment | Endpoint example |
|---|---|---|
| **Cloud API** (Azure AI Foundry) | Dev / staging | `https://<resource>.cognitiveservices.azure.com` |
| **Disconnected container** | Production on-prem | `http://localhost:5000` |

### Configuration

```python
class AzureDIConfig(BaseModel):
    endpoint: str = "https://placeholder.cognitiveservices.azure.com"
    api_key: str = ""
    features: list[str] = Field(
        default_factory=lambda: ["barcodes", "keyValuePairs"]
    )
```

The `features` list controls which add-on capabilities are requested. `barcodes` enables barcode extraction and `keyValuePairs` enables key-value pair detection (useful for forms).

---

## Client Initialisation

Like Marker, the Azure DI client is **lazily initialised** on first use:

```python
def _get_client(self):
    if self._client is not None:
        return self._client

    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.core.credentials import AzureKeyCredential

    self._client = DocumentIntelligenceClient(
        endpoint=self._config.endpoint,
        credential=AzureKeyCredential(self._config.api_key),
    )
    return self._client
```

The SDK import is deferred into the method body so the module can be loaded even if the `azure-ai-documentintelligence` package is not installed (useful for local dev with Marker-only runs).

---

## Analysis Flow

The adapter calls the `prebuilt-layout` model with the configured feature flags:

```python
poller = client.begin_analyze_document(
    "prebuilt-layout",
    analyze_request=AnalyzeDocumentRequest(bytes_source=pdf_bytes),
    features=self._config.features,
)
result = poller.result()
```

The call is wrapped in `run_in_executor` because the SDK's `begin_analyze_document` / `poller.result()` cycle is synchronous and involves network I/O:

```python
result = await loop.run_in_executor(None, _analyze)
```

---

## Extracted Data

### Per-Word Data

For every page, each word is mapped to an `OCRWord`:

```python
OCRWord(
    text=word.content,
    confidence=word.confidence,          # 0.0–1.0
    is_handwritten=word.is_handwritten,  # bool
    bounding_region=BoundingRegion(
        page_num=page_num,
        x=min(x_coords),
        y=min(y_coords),
        width=max(x_coords) - min(x_coords),
        height=max(y_coords) - min(y_coords),
    ),
)
```

The `BoundingRegion` is computed from the Azure polygon (list of x,y coordinate pairs) by taking the min/max extents.

### Barcodes

Each detected barcode is mapped to a `BarcodeResult`:

```python
BarcodeResult(
    barcode_type=bc.kind,   # e.g. "Code128", "QRCode", "EAN13"
    value=bc.value,         # decoded string
    page_num=page_num,
)
```

Azure DI supports 17+ barcode symbologies including Code 39, Code 93, Code 128, UPC-A, UPC-E, EAN-8, EAN-13, ITF, Codabar, Data Matrix, QR Code, PDF417, and more.

### Selection Marks

Checkboxes and radio buttons are extracted as `SelectionMark`:

```python
SelectionMark(
    state=sm.state,            # "selected" or "unselected"
    confidence=sm.confidence,  # 0.0–1.0
    page_num=page_num,
)
```

---

## Cross-Page Support

Azure DI's `bounding_regions` can reference multiple pages, enabling native cross-page table and field detection. The adapter currently extracts the first bounding region per word:

```python
def _to_bounding_region(regions, page_num):
    if not regions:
        return None
    r = regions[0]
    # ... compute from polygon ...
```

For tables, Azure DI natively tracks cell spans across pages via `bounding_regions` on each cell.

---

## Confidence Scoring (azure_di mode)

In `azure_di` pipeline mode, confidence comes directly from DI's per-word scores:

```
Page confidence = 0.50 × avg_word_confidence + 0.20 × min_word_confidence + 0.30 × validation_pass_rate
```

Pages below the HITL threshold are routed for human review.

---

## Capability Flags

```python
supports_handwriting()    → True
supports_barcodes()       → True
supports_selection_marks() → True
```

---

## Port Model Reference

The adapter uses these port models from `backend/app/core/ports/ocr.py`:

| Model | Purpose |
|---|---|
| `OCRWord` | Per-word text, confidence, handwriting flag, bounding box |
| `BarcodeResult` | Decoded barcode type + value per page |
| `SelectionMark` | Checkbox/radio state + confidence per page |
| `BoundingRegion` | Rectangular bounding box (page, x, y, width, height) |
| `OCRPageResult` | Aggregation of words, barcodes, selection marks per page |
| `OCRResult` | Collection of page results + full Markdown content |

---

## Related Pages

- [OCR engine overview](overview.md)
- [Marker adapter](marker.md)
- [Docling quality adapter](docling.md)
