# Data Flow: PDF Upload to Compliance Report

This document traces the complete lifecycle of a document through the system, from user upload to the final compliance dashboard.

## Pipeline Summary

```
Upload → Ingest → Parallel OCR+Quality → Merge → Confidence Routing
    → [HITL Review if needed] → Store → Compliance Subgraph → Dashboard
```

The pipeline is implemented as two LangGraph `StateGraph` instances:
1. **Document Graph** (`app/workflow/document_graph.py`) -- ingestion through storage
2. **Compliance Graph** (`app/workflow/compliance_graph.py`) -- ALCOA++, GMP, Checklist, SOP analysis

---

## Step-by-Step Flow

### Step 1: User Uploads PDF

The user selects a PDF via the Next.js frontend (`frontend/src/components/upload/document-upload.tsx`). The file is sent as a multipart upload to the FastAPI backend.

### Step 2: FastAPI Receives and Saves

The `/upload` endpoint (`app/api/routes/documents.py`):

1. Generates a `doc_id` (UUID4)
2. Creates the document directory at `{storage.base_path}/{doc_id}/`
3. Writes the raw PDF to disk
4. Returns `{ doc_id, filename, size_bytes, status: "uploaded" }`

The frontend establishes a WebSocket connection on `doc_id` to receive real-time progress updates.

### Step 3: LangGraph Workflow Starts

The Document Graph is invoked with initial `DocumentState`:

```python
{
    "doc_id": "abc-123-...",
    "pdf_path": "/data/documents/abc-123-.../document.pdf",
    "filename": "batch-record.pdf",
    "total_pages": 0,         # set during ingest
    "marker_results": {},
    "azure_di_results": {},
    "quality_scores": {},
    "extractions": [],
    "confidence_scores": {},
    "status": "uploaded",
}
```

### Step 4: Ingest Node

The `ingest_document` node validates the PDF and determines the page count:

1. Checks that the PDF file exists at `pdf_path`
2. Opens with PyMuPDF (`fitz`) to count pages
3. Sends a WebSocket notification: `{ type: "status", status: "ingested", total_pages: N }`
4. Returns `{ total_pages: N, status: "ingested" }`

If the PDF is missing or corrupt, the workflow routes to `handle_error`.

### Step 5: Parallel Fan-Out (Send)

The `route_after_ingest` conditional edge returns three `Send` objects, triggering parallel execution:

```python
[
    Send("run_marker_ocr", state),      # Primary OCR
    Send("run_azure_di_ocr", state),    # Secondary OCR
    Send("run_quality_scoring", state), # Quality assessment
]
```

All three run concurrently. Each sends WebSocket status updates as it progresses.

#### 5a: Marker OCR

- Calls `container.primary_ocr.extract(pdf_path)` via the `OCREngine` port
- Marker's `PdfConverter` processes the full PDF (runs in executor for async compat)
- Splits paginated markdown output on `\n\n---\n\n`
- Returns per-page markdown and word counts in `marker_results`

#### 5b: Azure DI OCR

- Calls `container.secondary_ocr.extract(pdf_path)` via the `OCREngine` port
- Sends the PDF to Azure Document Intelligence `prebuilt-layout` model
- Extracts per-word confidence scores, handwriting flags, barcodes, and selection marks
- Returns structured per-page data in `azure_di_results`

#### 5c: Docling Quality Scoring

- Calls `container.quality_scorer.score(pdf_path)` via the `QualityScorer` port
- Docling's `DocumentConverter` produces per-page scores for layout, table, OCR, and parse quality
- Returns a `QualityReport` serialized into `quality_scores`

### Step 6: Merge Node

All three parallel branches converge at `merge_ocr_results`. For each page, the node:

1. Combines Marker's markdown with Azure DI's word-level data (handwriting counts, barcodes, selection marks)
2. Computes a **composite confidence score** using weighted factors:

| Factor | Weight | Source |
|--------|--------|--------|
| Docling mean quality | 0.30 | Average of layout, table, OCR, parse scores |
| Azure DI min word confidence | 0.25 | Lowest per-word confidence on the page |
| Marker table quality | 0.15 | Table detection score (1-5 scale, normalized) |
| Validation rules | 0.30 | Custom rule engine (placeholder at 0.8) |

3. Produces unified `extractions` list and `confidence_scores` dict

### Step 7: Confidence Routing

The `route_by_confidence` conditional edge examines the minimum confidence score across all pages:

| Confidence Range | Route | Behavior |
|-----------------|-------|----------|
| >= 0.9 | `auto_approve` | All pages approved automatically |
| 0.7 - 0.9 | `auto_approve` | Approved (batch review available if enabled) |
| < 0.7 | `hitl_review` | Human review required |

Thresholds are configured in `settings.hitl`:

```yaml
hitl:
  auto_approve_threshold: 0.9
  review_threshold: 0.7
  batch_review_enabled: true
```

### Step 8: HITL Review (if needed)

When pages fall below the review threshold:

1. The `hitl_review` node collects all pages needing review, sorted by confidence (lowest first)
2. Sends a WebSocket notification: `{ type: "hitl_required", pages_count: N, pages: [...] }`
3. Calls `interrupt()` with the review payload -- this **pauses the workflow**
4. The frontend renders the review UI (`frontend/src/components/review/review-interface.tsx`)

### Step 9: Human Reviews and Resumes

The reviewer sees:
- Original PDF page
- Extracted markdown
- Confidence scores and quality grades
- Handwritten content flags
- Barcode and selection mark data

The reviewer can edit text, approve, or reject. On submission, the frontend sends:

```python
Command(resume={
    "approved_pages": [...],
    "edited_pages": {...},
    "rejected_pages": [...]
})
```

The workflow resumes from the interrupt point with the feedback in `hitl_decisions`.

### Step 10: Store Results

The `store_results` node:

1. Constructs a `DigitalDocument` from the workflow state (metadata, raw markdown, sections)
2. Calls `container.document_store.save_document(doc)` via the `DocumentStore` port
3. Sends a WebSocket notification: `{ type: "status", status: "completed" }`

### Step 11: Compliance Subgraph

The Compliance Graph is invoked with the document's extractions and sections. It fans out to four parallel agents via `Send`:

```python
[
    Send("alcoa_review", state),       # ALCOA++ compliance
    Send("gmp_review", state),         # GMP compliance
    Send("checklist_review", state),   # Checklist-based review
    Send("sop_review", state),         # BMR review against SOP
]
```

Each agent:
1. Receives the document extractions
2. Uses the `LLMProvider` port (via `container.llm`) to analyze content against its ruleset
3. Returns a list of findings with `rule_id`, `severity`, `page_num`, `description`, and `recommendation`

### Step 12: Aggregate Findings

The `aggregate_findings` node:

1. Combines all findings from the four agents
2. Computes a compliance score (starts at 100, deducts by severity):

| Severity | Deduction |
|----------|-----------|
| Critical | -10 |
| Major | -5 |
| Minor | -2 |
| Observation | -1 |

3. Returns `aggregated_findings` and `compliance_score`

### Step 13: Frontend Dashboard

The compliance dashboard (`frontend/src/components/compliance/compliance-dashboard.tsx`) displays:
- Overall compliance score
- Findings grouped by agent (ALCOA++, GMP, Checklist, SOP)
- Per-page findings with severity indicators
- Recommendations for remediation

---

## Sequence Diagram

```mermaid
sequenceDiagram
    actor User
    participant Frontend as Next.js Frontend
    participant API as FastAPI
    participant WS as WebSocket
    participant Graph as LangGraph<br/>Document Graph
    participant Marker as Marker OCR<br/>(primary_ocr)
    participant AzureDI as Azure DI OCR<br/>(secondary_ocr)
    participant Docling as Docling<br/>(quality_scorer)
    participant LLM as Ollama LLM<br/>(llm)
    participant Store as DocumentStore
    participant CompGraph as LangGraph<br/>Compliance Graph

    User->>Frontend: Select PDF file
    Frontend->>API: POST /upload (multipart)
    API->>Store: save_file(pdf_bytes)
    API-->>Frontend: { doc_id, status: uploaded }

    Frontend->>WS: Connect WebSocket(doc_id)
    Frontend->>API: POST /process/{doc_id}
    API->>Graph: invoke(DocumentState)

    Note over Graph: ingest_document node
    Graph->>WS: { status: ingested, total_pages: N }

    Note over Graph: Parallel fan-out via Send
    par Marker OCR
        Graph->>Marker: extract(pdf_path)
        Marker-->>Graph: OCRResult (markdown per page)
    and Azure DI OCR
        Graph->>AzureDI: extract(pdf_path)
        AzureDI-->>Graph: OCRResult (words, barcodes, marks)
    and Quality Scoring
        Graph->>Docling: score(pdf_path)
        Docling-->>Graph: QualityReport (per-page scores)
    end

    Note over Graph: merge_ocr_results node
    Graph->>Graph: Compute composite confidence per page
    Graph->>WS: { status: merging_results }

    alt confidence >= 0.7 (all pages)
        Note over Graph: auto_approve node
        Graph->>WS: { status: auto_approved }
    else confidence < 0.7 (some pages)
        Note over Graph: hitl_review node
        Graph->>WS: { type: hitl_required, pages: [...] }
        Graph->>Graph: interrupt() — workflow paused

        WS-->>Frontend: Show review UI
        User->>Frontend: Review, edit, approve pages
        Frontend->>API: POST /review/{doc_id} (feedback)
        API->>Graph: Command(resume=feedback)
        Graph->>WS: { status: reviewed }
    end

    Note over Graph: store_results node
    Graph->>Store: save_document(DigitalDocument)
    Graph->>WS: { status: completed }

    Note over CompGraph: Compliance subgraph starts
    par ALCOA++ Review
        CompGraph->>LLM: generate_structured(extractions)
        LLM-->>CompGraph: ALCOA findings
    and GMP Review
        CompGraph->>LLM: generate_structured(extractions)
        LLM-->>CompGraph: GMP findings
    and Checklist Review
        CompGraph->>LLM: generate_structured(extractions)
        LLM-->>CompGraph: Checklist findings
    and SOP Review
        CompGraph->>LLM: generate_structured(extractions)
        LLM-->>CompGraph: SOP findings
    end

    Note over CompGraph: aggregate_findings node
    CompGraph->>CompGraph: Compute compliance score
    CompGraph->>WS: { compliance_score, findings }
    WS-->>Frontend: Update compliance dashboard
    Frontend-->>User: Display compliance report
```

---

## State Transitions

The `DocumentState.status` field tracks progress:

```
uploaded → ingested → marker_ocr_running → marker_complete
                    → azure_di_running   → azure_di_complete
                    → quality_scoring    → quality_scored
         → merging_results → merged
         → auto_approved / reviewed
         → completed
```

Error states: `marker_error`, `azure_di_error`, `quality_error`, `error`

---

## Key Design Decisions

1. **Parallel OCR, not sequential** -- Marker and Azure DI extract different things (markdown vs. word-level confidence). Running them in parallel via `Send` halves the wall-clock time.

2. **Quality scoring is independent** -- Docling runs alongside OCR, not after it. Its scores are used for confidence calculation, not for content extraction.

3. **Composite confidence, not single-source** -- No single engine's confidence is trusted alone. The weighted formula combines four signals to reduce false positives in HITL routing.

4. **`interrupt()` for HITL** -- LangGraph's native interrupt/resume mechanism persists workflow state to the checkpointer. The workflow can be paused for hours or days without losing progress.

5. **Compliance agents are embarrassingly parallel** -- ALCOA++, GMP, Checklist, and SOP reviews are independent. Fan-out via `Send` runs all four concurrently against the LLM.

---

## Related Pages

- [Architecture Overview](overview.md) -- System architecture and layer diagram
- [Ports & Adapters](ports-and-adapters.md) -- Port contracts invoked at each step
- [Deployment Environments](deployment-environments.md) -- How adapters differ per environment
- [Back to Wiki Home](../README.md)
