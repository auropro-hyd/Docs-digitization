# Wiki — Technical Documentation

> Back to [Project README](../README.md)

## Contents

### Architecture

| Document | Description |
|----------|-------------|
| [Architecture Overview](architecture/overview.md) | Hexagonal architecture, layer breakdown, system diagram |
| [Ports & Adapters](architecture/ports-and-adapters.md) | 6 port contracts, Protocol definitions, adapter implementations |
| [Pipeline Modes](pipeline-modes.md) | `azure_di` vs `marker_docling` vs `datalab` — comparison and switching |
| [Azure DI Setup](azure-di-setup.md) | Cloud API and disconnected container setup |
| [Deployment Environments](architecture/deployment-environments.md) | Dev, staging, and on-prem production configs |
| [Data Flow](architecture/data-flow.md) | End-to-end pipeline from upload to compliance report |

### Backend — OCR Engines

| Document | Description |
|----------|-------------|
| [OCR Engine Strategy](backend/ocr-engines/overview.md) | Engine capabilities, mode comparison, confidence formulas |
| [Marker](backend/ocr-engines/marker.md) | PDF-to-Markdown, LLM processors, cross-page table merging |
| [Azure Document Intelligence](backend/ocr-engines/azure-di.md) | Handwriting, barcodes, selection marks, per-word confidence |
| [Docling](backend/ocr-engines/docling.md) | Quality scoring: layout, table, OCR, parse (MIT, CPU-only) |

### Backend — Workflow

| Document | Description |
|----------|-------------|
| [Document Processing](backend/workflow/document-processing.md) | LangGraph state-graph, mode-based conditional routing |
| [Document Segmentation & BPCR Detection](backend/workflow/segmentation.md) | Spec 011 pipeline: LLM segmentation + deterministic post-pipeline + BPCR sub-section detection + HITL overrides |
| [Compliance Review](backend/workflow/compliance-review.md) | ALCOA++, GMP, SOP verification agents |
| [HITL Flow](backend/workflow/hitl-flow.md) | Interrupt/resume, review queue, audit trail |
| [VLM Provider](backend/vlm-provider.md) | Vision-Language Model port + Gemini / vLLM adapters; visual checks; grayscale gate; absence-first prompts |
| [Report Renderer](backend/report-renderer.md) | Spec 008 PDF / HTML / Markdown export — five-column rule table, three-state taxonomy, WeasyPrint, versioned cache |
| [Rule Authoring Playbook](rule_authoring_playbook.md) | Operator guide for config-first rule updates and QA checks |

### Backend — Confidence & Config

| Document | Description |
|----------|-------------|
| [Composite Scorer](backend/confidence-scoring/composite-scorer.md) | Mode-specific confidence: DI word scores or Docling quality |
| [Validation Rules](backend/confidence-scoring/validation-rules.md) | Date, quantity, and content plausibility checks |
| [Settings](backend/configuration/settings.md) | YAML structure, env overrides, priority order |
| [Dependency Injection](backend/configuration/dependency-injection.md) | DI container, adapter wiring, match/case dispatch |

### Frontend

| Document | Description |
|----------|-------------|
| [Frontend Overview](frontend/overview.md) | Next.js app structure, routing, Zustand state |
| [Upload Flow](frontend/upload-flow.md) | Drag-and-drop, progress tracking, validation |
| [Review Interface](frontend/review-interface.md) | Split-pane HITL, inline editing, VLM findings, keyboard shortcuts |
| [Compliance Dashboard](frontend/compliance-dashboard.md) | ALCOA++ visualizations, severity breakdown, visual evidence viewer |
| [BMR Runs UI](frontend/bmr-runs.md) | `/bmr/runs` runs list + `/bmr/runs/{id}` detail with live stage progress, BPCR sections panel, findings summary |
| [Corrections Manager](frontend/corrections-manager.md) | OCR correction rules, confusion chart, rule management |
| [WebSocket Streaming](frontend/websocket-streaming.md) | Real-time updates from LangGraph to browser |

### DevOps

| Document | Description |
|----------|-------------|
| [Local Setup](devops/local-setup.md) | Prerequisites, first-run walkthrough |
| [GitHub Actions CI](devops/github-actions-ci.md) | CI workflow, PR-quality gate, weekly maintenance, branch-protection rules, dependabot |
| [Quick Commands](quick_commands.md) | Copy-paste reference for all dev commands |

---

## Suggested Reading Order

1. [Architecture Overview](architecture/overview.md)
2. [Ports & Adapters](architecture/ports-and-adapters.md)
3. [Pipeline Modes](pipeline-modes.md)
4. [Data Flow](architecture/data-flow.md)
5. [OCR Engine Strategy](backend/ocr-engines/overview.md)
6. [Document Processing](backend/workflow/document-processing.md)
7. [Document Segmentation & BPCR Detection](backend/workflow/segmentation.md)
8. [Compliance Review](backend/workflow/compliance-review.md)
9. [VLM Provider](backend/vlm-provider.md)
10. [Report Renderer](backend/report-renderer.md)
11. [BMR Runs UI](frontend/bmr-runs.md)
12. [Composite Scorer](backend/confidence-scoring/composite-scorer.md)
13. [HITL Flow](backend/workflow/hitl-flow.md)
14. [Settings](backend/configuration/settings.md)
15. [Local Setup](devops/local-setup.md)
16. [GitHub Actions CI](devops/github-actions-ci.md)
