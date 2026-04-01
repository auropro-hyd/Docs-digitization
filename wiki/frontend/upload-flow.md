# Upload Flow & Processing Dashboard

The upload page (`/`) combines two components: a drag-and-drop uploader and a real-time processing dashboard that streams pipeline progress via WebSocket.

## Components

### DocumentUpload

**File:** `frontend/src/components/upload/document-upload.tsx`

The upload widget provides:

- **Drag-and-drop zone** with visual feedback (border color change on drag-over)
- **File browse button** as a fallback
- **PDF-only validation** — rejects non-`.pdf` files with an error message
- **Loading state** — spinner and disabled interaction during upload

#### Upload Sequence

```
User drops/selects file
  → Validate file extension (.pdf only)
  → POST /api/documents/upload (multipart/form-data)
  → Receive { doc_id, filename }
  → Store doc_id + filename in Zustand (triggers status → "uploading")
  → ProcessingDashboard takes over
```

```typescript
const handleFile = useCallback(async (file: File) => {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    setError("Only PDF files are supported");
    return;
  }
  setIsUploading(true);
  try {
    const result = await uploadDocument(file);
    setDocId(result.doc_id, result.filename);
  } catch (err) {
    setError(err instanceof Error ? err.message : "Upload failed");
  } finally {
    setIsUploading(false);
  }
}, [setDocId, setError]);
```

### ProcessingDashboard

**File:** `frontend/src/components/upload/processing-dashboard.tsx`

Renders once a `docId` is set in the store. Shows real-time processing status via WebSocket updates.

#### Visual Elements

| Element | Description |
|---------|-------------|
| **File info** | Document icon, filename, page count |
| **Status indicator** | `StatusIndicator` component showing current processing stage |
| **Error banner** | Red alert when `error` is set in the store |
| **Stat cards** (3-column grid) | Total Pages, Approved, Needs Review — each with icon and count |
| **Page progress dots** | One small colored square per page, color-coded by status |
| **Progress bar** | Percentage bar based on approved / total ratio |

#### Page Dot Color Coding

| Status | Color | Animation |
|--------|-------|-----------|
| `queued` | Gray (`bg-gray-200`) | — |
| `extracting` | Blue (`bg-blue-300`) | Pulse |
| `scoring` | Purple (`bg-purple-300`) | Pulse |
| `reviewing` | Amber (`bg-amber-400`) | — |
| `approved` | Green (`bg-green-400`) | — |
| `flagged` | Red (`bg-red-400`) | — |
| `error` | Dark red (`bg-red-600`) | — |

Each dot has a tooltip showing `Page N: status (confidence%)`.

## Processing Status Progression

The backend pipeline emits status updates through WebSocket as the document moves through the LangGraph workflow:

```
idle
  → uploading              (file sent to server)
  → ingested               (stored, metadata extracted)
  → marker_ocr_running     (OCR extraction processing)
  → azure_di_running       (OCR extraction processing)
  → quality_scoring        (quality assessment)
  → merging_results        (combining OCR outputs)
  → hitl_required          (low-confidence pages need human review)
    OR auto_approved       (all pages above threshold)
  → reviewed               (HITL complete)
  → completed              (pipeline finished)
```

If an error occurs at any stage, the status transitions to `error` with a message.

## WebSocket Integration

The `useDocumentWebSocket` hook connects to the backend when a `docId` is available:

```typescript
export function useDocumentWebSocket(docId: string | null) {
  const wsRef = useRef<DocumentWebSocket | null>(null);
  const { setProcessingStatus, setTotalPages, setError } = useDocumentStore();

  useEffect(() => {
    if (!docId) return;
    const ws = new DocumentWebSocket(docId);
    wsRef.current = ws;

    const unsubscribe = ws.subscribe((msg: WSMessage) => {
      if (msg.type === "status" && msg.status) {
        setProcessingStatus(msg.status as any);
        if (msg.total_pages) setTotalPages(msg.total_pages);
      }
      if (msg.type === "error") setError(msg.error || "Unknown error");
      if (msg.type === "hitl_required") setProcessingStatus("hitl_required");
    });

    ws.connect();
    return () => { unsubscribe(); ws.disconnect(); };
  }, [docId, setProcessingStatus, setTotalPages, setError]);
}
```

**File:** `frontend/src/hooks/useWebSocket.ts`

> For full WebSocket architecture details, see [WebSocket Streaming](./websocket-streaming.md).

## Data Flow Diagram

```
┌────────────┐     POST /api/documents/upload     ┌────────────┐
│  Document   │ ──────────────────────────────────▶│   FastAPI   │
│   Upload    │     { doc_id, filename }           │   Backend   │
└──────┬─────┘ ◀──────────────────────────────────│            │
       │                                           └──────┬─────┘
       ▼                                                  │
┌────────────┐                                           │
│  Zustand    │◀── setDocId(doc_id, filename)             │
│   Store     │                                           │
└──────┬─────┘                                           │
       │                                                  ▼
       ▼                                           ┌────────────┐
┌────────────┐     ws://host/ws/{doc_id}          │  LangGraph  │
│ Processing  │◀─── WebSocket status messages ────│  Workflow   │
│  Dashboard  │                                    └────────────┘
└────────────┘
```

## Related Pages

- [Frontend Overview](./overview.md) — Architecture and tech stack
- [WebSocket Streaming](./websocket-streaming.md) — Real-time communication details
- [Review Interface](./review-interface.md) — Where HITL review happens after processing
- [Settings](../backend/configuration/settings.md) — HITL threshold configuration
