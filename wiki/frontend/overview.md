# Frontend Architecture Overview

## Tech Stack

| Technology | Version | Purpose |
|------------|---------|---------|
| [Next.js](https://nextjs.org/) | 16 (App Router) | React framework with server-side rendering and file-based routing |
| [React](https://react.dev/) | 19 | UI component library |
| [TypeScript](https://www.typescriptlang.org/) | 5.x | Static type checking |
| [Tailwind CSS](https://tailwindcss.com/) | 4 | Utility-first CSS framework |
| [Zustand](https://github.com/pmndrs/zustand) | 5 | Lightweight client-side state management |
| [Lucide React](https://lucide.dev/) | 0.575+ | Icon library |

## Application Pages

The frontend uses Next.js App Router with four primary routes:

| Route | Page | Description |
|-------|------|-------------|
| `/` | Upload | Drag-and-drop PDF upload with real-time processing dashboard |
| `/documents` | Document List | Browse all uploaded documents and their processing status |
| `/review` | HITL Review | Split-pane human-in-the-loop review interface for extracted data |
| `/compliance` | Compliance Dashboard | Compliance scoring, severity breakdown, and findings per document |

Each route maps to a page component in `frontend/src/app/`:

```
frontend/src/app/
├── page.tsx              # / — Upload page
├── layout.tsx            # Root layout (global nav, providers)
├── globals.css           # Tailwind base styles
├── documents/
│   └── page.tsx          # /documents — List view
├── review/
│   └── page.tsx          # /review — HITL review
└── compliance/
    └── page.tsx          # /compliance — Dashboard
```

## Component Structure

Components are organized by feature domain under `frontend/src/components/`:

```
components/
├── upload/
│   ├── document-upload.tsx       # Drag-and-drop upload widget
│   └── processing-dashboard.tsx  # Real-time processing status display
├── review/
│   └── review-interface.tsx      # Split-pane HITL review
├── compliance/
│   └── compliance-dashboard.tsx  # Compliance score, findings, categories
└── common/
    ├── pdf-viewer.tsx            # PDF viewer wrapper with responsive behavior
    ├── pdf-viewer-inner.tsx      # Page rendering internals
    ├── confidence-badge.tsx       # Color-coded confidence score badge
    └── status-indicator.tsx       # Processing status pill/indicator
```

## User-Facing Terminology Policy

The UI presents a product-level experience and avoids exposing underlying vendor engines.

- Use neutral labels such as **OCR Extraction**, **Quality Scoring**, and **Processing Complete**.
- Keep provider-specific names in internal config, logs, and developer documentation only.
- Sanitize runtime progress text before rendering in UI surfaces (status badges, progress bars, and notifications).

## State Management

All client-side state is managed through a single **Zustand store** in `frontend/src/stores/document-store.ts`.

The store tracks:

- `docId` / `filename` — Active document identifiers
- `processingStatus` — Current pipeline stage (see [Upload Flow](./upload-flow.md))
- `totalPages` — Page count for the document
- `pages` — `Map<number, PageData>` with per-page confidence, status, markdown, and extraction data
- `error` — Current error message, if any

Actions: `setDocId`, `setProcessingStatus`, `setTotalPages`, `updatePage`, `setError`, `reset`.

```typescript
type ProcessingStatus =
  | "idle" | "uploading" | "ingested"
  | "marker_ocr_running" | "azure_di_running"
  | "quality_scoring" | "merging_results"
  | "hitl_required" | "auto_approved"
  | "reviewed" | "completed" | "error";

type PageStatus =
  | "queued" | "extracting" | "scoring"
  | "reviewing" | "approved" | "flagged" | "error";
```

> See [WebSocket Streaming](./websocket-streaming.md) for how the store receives real-time updates.

## Real-Time Updates

The `DocumentWebSocket` class (`frontend/src/lib/websocket.ts`) manages a persistent WebSocket connection to the FastAPI backend at `/ws/{doc_id}`. It provides:

- **Auto-reconnect** with exponential backoff (up to 5 attempts)
- **Pub/sub** via `subscribe(callback)` returning an unsubscribe function
- **Typed messages** (`WSMessage`) dispatched to all subscribers

The `useDocumentWebSocket` hook (`frontend/src/hooks/useWebSocket.ts`) wires the WebSocket connection to the Zustand store — subscribing on mount, dispatching status/error updates, and cleaning up on unmount.

> Full details in [WebSocket Streaming](./websocket-streaming.md).

## API Client

`frontend/src/lib/api.ts` provides a thin REST client wrapping `fetch()`:

| Function | Method | Endpoint | Description |
|----------|--------|----------|-------------|
| `uploadDocument(file)` | POST | `/api/documents/upload` | Upload a PDF file |
| `getDocument(docId)` | GET | `/api/documents/{docId}` | Fetch document metadata |
| `listDocuments()` | GET | `/api/documents/` | List all documents |
| `getReviewPages(docId)` | GET | `/api/review/{docId}/pages` | Get pages pending review |
| `approvePage(docId, pageNum)` | POST | `/api/review/{docId}/pages/{pageNum}/approve` | Approve a single page |
| `getComplianceReport(docId)` | GET | `/api/compliance/{docId}/report` | Get compliance report |
| `runComplianceReview(docId)` | POST | `/api/compliance/{docId}/run` | Trigger compliance analysis |

Base URL is configured via `NEXT_PUBLIC_API_URL` (defaults to `http://localhost:8100`).

## Utility Functions

`frontend/src/lib/utils.ts` exports the `cn()` helper that merges Tailwind classes using `clsx` + `tailwind-merge`, preventing class conflicts.

## Related Pages

- [Upload Flow](./upload-flow.md) — Document upload and processing dashboard
- [Review Interface](./review-interface.md) — Split-pane HITL review
- [Compliance Dashboard](./compliance-dashboard.md) — Compliance scoring and findings
- [WebSocket Streaming](./websocket-streaming.md) — Real-time communication architecture
- [Local Setup](../devops/local-setup.md) — Development environment setup
