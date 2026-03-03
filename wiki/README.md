# Auto Transcription Platform Wiki

**End-to-End Document Digitalization Platform** for pharmaceutical batch manufacturing records (BMRs). Converts scanned/handwritten PDFs into structured digital records with full ALCOA++, GMP, and SOP compliance verification.

> **Quick start?** See the [Quick Commands Reference](quick_commands.md) for copy-paste commands.

## Architecture at a Glance

The platform follows **Hexagonal Architecture (Ports & Adapters)**. The core domain has zero external dependencies; every external system (OCR engines, LLMs, storage backends, notification channels) is accessed through Protocol interfaces. A central DI container wires concrete adapters per environment.

```
Frontend (Next.js) ─── FastAPI ─── LangGraph Workflow
                                        │
                                   Core Domain
                                   (zero deps)
                                        │
                                      Ports
                                   (Protocols)
                                     ╱    ╲
                                Adapters   Adapters
                               (Marker)   (Azure DI)
                                     ╲    ╱
                                 Config / DI
                              (settings.yaml)
```

## Table of Contents

### Architecture

| Document | Description |
|----------|-------------|
| [Architecture Overview](architecture/overview.md) | Hexagonal architecture rationale, layer breakdown, and system diagram |
| [Ports & Adapters](architecture/ports-and-adapters.md) | All 5 ports, their Protocol definitions, and adapter implementations |
| [Deployment Environments](architecture/deployment-environments.md) | Local dev, Azure staging, and on-prem production configurations |
| [Data Flow](architecture/data-flow.md) | End-to-end pipeline from PDF upload to compliance report |

### Backend

#### OCR Engines

| Document | Description |
|----------|-------------|
| [OCR Engines Overview](backend/ocr-engines/overview.md) | Comparison of OCR engines, selection criteria, and fallback strategy |
| [Marker](backend/ocr-engines/marker.md) | Primary OCR engine -- PDF-to-Markdown with LLM-powered processors |
| [Azure Document Intelligence](backend/ocr-engines/azure-di.md) | Secondary OCR for handwriting, barcodes, and selection marks |
| [Docling](backend/ocr-engines/docling.md) | MIT-licensed quality scoring engine (CPU-only) |

#### Workflow

| Document | Description |
|----------|-------------|
| [Document Processing](backend/workflow/document-processing.md) | LangGraph state-graph pipeline from PDF upload to structured output |
| [Compliance Review](backend/workflow/compliance-review.md) | ALCOA++, GMP, and SOP verification agents and checklists |
| [HITL Flow](backend/workflow/hitl-flow.md) | Human-in-the-loop review queue, interrupt/resume mechanics, and audit trail |

#### Confidence Scoring

| Document | Description |
|----------|-------------|
| [Composite Scorer](backend/confidence-scoring/composite-scorer.md) | Multi-signal confidence scoring algorithm and thresholds |
| [Validation Rules](backend/confidence-scoring/validation-rules.md) | Field-level and page-level validation rule definitions |

#### Configuration

| Document | Description |
|----------|-------------|
| [Settings](backend/configuration/settings.md) | `settings.yaml` structure, environment overrides, and secrets handling |
| [Dependency Injection](backend/configuration/dependency-injection.md) | DI container wiring, adapter registration, and per-environment bindings |

### Frontend

| Document | Description |
|----------|-------------|
| [Frontend Overview](frontend/overview.md) | Next.js app structure, routing, and shared components |
| [Upload Flow](frontend/upload-flow.md) | PDF upload UX, drag-and-drop, progress tracking, and validation |
| [Review Interface](frontend/review-interface.md) | Side-by-side OCR review, inline editing, and approval workflow |
| [Compliance Dashboard](frontend/compliance-dashboard.md) | ALCOA++ and GMP compliance visualizations and filtering |
| [WebSocket Streaming](frontend/websocket-streaming.md) | Real-time progress updates and live document streaming |

### DevOps

| Document | Description |
|----------|-------------|
| [Local Setup](devops/local-setup.md) | Prerequisites, environment setup, and first-run walkthrough |
| [Azure DevOps Pipeline](devops/azure-devops-pipeline.md) | CI/CD pipeline stages, triggers, and deployment strategy |

---

## How to Read This Wiki

### Phase 1: Understand the Architecture

1. **[Architecture Overview](architecture/overview.md)** -- Start here. Why Hexagonal, how the three layers interact, and the role of LangGraph.
2. **[Ports & Adapters](architecture/ports-and-adapters.md)** -- The five port contracts (OCREngine, LLMProvider, QualityScorer, DocumentStore, NotificationPort) and every adapter.
3. **[Data Flow](architecture/data-flow.md)** -- Follow a PDF through all 13 steps from upload to compliance report.
4. **[Deployment Environments](architecture/deployment-environments.md)** -- How config switches adapters across local dev, Azure staging, and on-prem production.

### Phase 2: Understand the OCR Pipeline

5. **[OCR Engines Overview](backend/ocr-engines/overview.md)** -- Why three engines, what each excels at, and how they run in parallel.
6. **[Marker](backend/ocr-engines/marker.md)** -- Primary OCR: PDF-to-Markdown, 9 LLM processors, cross-page table merging, OllamaService.
7. **[Azure Document Intelligence](backend/ocr-engines/azure-di.md)** -- Secondary OCR: handwriting detection, barcodes, selection marks, per-field confidence.
8. **[Docling](backend/ocr-engines/docling.md)** -- Quality scoring only: layout, table, OCR, and parse scores per page.

### Phase 3: Understand the Workflow

9. **[Document Processing Workflow](backend/workflow/document-processing.md)** -- The main LangGraph graph: nodes, edges, parallel fan-out, conditional routing.
10. **[Composite Confidence Scorer](backend/confidence-scoring/composite-scorer.md)** -- How the four signal sources combine into a single confidence score per page.
11. **[Validation Rules](backend/confidence-scoring/validation-rules.md)** -- Custom plausibility checks: dates, quantities, empty pages, and how to add new rules.
12. **[HITL Flow](backend/workflow/hitl-flow.md)** -- Confidence-based routing, interrupt/resume mechanics, review queue, audit trail.
13. **[Compliance Review](backend/workflow/compliance-review.md)** -- The compliance subgraph: ALCOA++, GMP, Checklist, and SOP agents in parallel.

### Phase 4: Understand the Frontend

14. **[Frontend Overview](frontend/overview.md)** -- Next.js app structure, routing, Zustand state, component organization.
15. **[Upload Flow](frontend/upload-flow.md)** -- Drag-and-drop upload, real-time processing dashboard, status progression.
16. **[WebSocket Streaming](frontend/websocket-streaming.md)** -- How LangGraph streams updates directly to the browser with no message broker.
17. **[Review Interface](frontend/review-interface.md)** -- Split-pane HITL: PDF left, extracted data right, keyboard shortcuts, confidence badges.
18. **[Compliance Dashboard](frontend/compliance-dashboard.md)** -- Score card, severity breakdown, category drill-down, findings list.

### Phase 5: Configuration and DevOps

19. **[Settings](backend/configuration/settings.md)** -- Central config: Pydantic Settings, per-environment YAML, environment variable overrides.
20. **[Dependency Injection](backend/configuration/dependency-injection.md)** -- DI container: factory functions, match/case dispatch, FastAPI Depends() wiring.
21. **[Local Setup](devops/local-setup.md)** -- Step-by-step guide to get the full stack running locally.
22. **[Azure DevOps Pipeline](devops/azure-devops-pipeline.md)** -- CI/CD: build, test, deploy to Azure App Service staging.
23. **[Quick Commands](quick_commands.md)** -- Copy-paste reference for all common dev commands.

## Repository Layout

```
Auto_Transcription/
├── backend/
│   └── app/
│       ├── core/              # Domain models, ports, services (zero external deps)
│       │   ├── models/        # DigitalDocument, QualityReport, elements
│       │   ├── ports/         # OCREngine, LLMProvider, QualityScorer, DocumentStore, NotificationPort
│       │   └── services/      # Confidence scoring, HITL routing, page classification
│       ├── adapters/          # Concrete implementations of ports
│       │   ├── ocr/           # MarkerOCRAdapter, AzureDIOCRAdapter
│       │   ├── llm/           # OllamaLLMAdapter, AzureOpenAILLMAdapter
│       │   ├── quality/       # DoclingQualityAdapter
│       │   ├── storage/       # FileSystemAdapter, AzureBlobAdapter
│       │   └── notification/  # WebSocketNotifyAdapter, PGListenNotifyAdapter
│       ├── config/            # Settings loader, DI container
│       ├── workflow/          # LangGraph graphs (document + compliance)
│       ├── compliance/        # ALCOA++, GMP, Checklist, SOP agents
│       ├── hitl/              # Review queue, audit trail
│       └── api/               # FastAPI routes, WebSocket manager
├── frontend/                  # Next.js app (upload, review, compliance dashboard)
├── infra/                     # Docker, Azure DevOps pipeline, setup scripts
└── wiki/                      # This documentation
```

## Key Technologies

| Layer | Technology |
|-------|-----------|
| API | FastAPI (async, WebSocket) |
| Workflow | LangGraph (StateGraph, Send, interrupt/Command) |
| Primary OCR | Marker v1.10+ (PDF-to-Markdown, LLM-powered processors) |
| Secondary OCR | Azure Document Intelligence (handwriting, barcodes, selection marks) |
| Quality Scoring | Docling (MIT, CPU-only) |
| LLM (production) | Ollama (on-prem, gemma2:9b) |
| LLM (dev fallback) | Azure OpenAI (GPT-4o via Azure AI Foundry) |
| Database | PostgreSQL (asyncpg) |
| Frontend | Next.js, TypeScript, Tailwind CSS |
| CI/CD | Azure DevOps Pipelines |
