# WebSocket Real-Time Streaming

The application uses WebSocket connections for real-time communication between the LangGraph processing pipeline and the browser. No Redis or external message broker is needed — updates flow directly from the backend through FastAPI's native WebSocket support.

## Architecture

```
┌──────────────┐      astream()      ┌──────────────┐     WebSocket      ┌──────────────┐
│   LangGraph  │ ──────────────────▶ │   FastAPI     │ ──────────────────▶│   Browser     │
│   Workflow   │   status updates    │   WebSocket   │   JSON messages    │   React App   │
│              │                     │   Endpoint    │                    │              │
└──────────────┘                     └──────────────┘                    └──────────────┘
                                      /ws/{doc_id}                        DocumentWebSocket
                                      ConnectionManager                   useDocumentWebSocket
```

**Why no broker?** LangGraph's `astream()` yields state updates as async events. The FastAPI WebSocket endpoint subscribes to these events and forwards them to connected clients in the same process. For a single-server deployment this avoids the complexity of Redis pub/sub or a message queue.

## Backend: ConnectionManager

**File:** `backend/app/api/websocket.py`

The `ConnectionManager` class maintains a dictionary of active WebSocket connections keyed by `doc_id`:

```python
class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, doc_id: str, websocket: WebSocket):
        await websocket.accept()
        self._connections.setdefault(doc_id, []).append(websocket)

    def disconnect(self, doc_id: str, websocket: WebSocket):
        conns = self._connections.get(doc_id, [])
        if websocket in conns:
            conns.remove(websocket)
        if not conns:
            self._connections.pop(doc_id, None)

    async def broadcast(self, doc_id: str, data: dict):
        conns = self._connections.get(doc_id, [])
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(doc_id, ws)
```

Key behaviors:
- **Per-document connections** — multiple clients can watch the same document
- **Dead connection cleanup** — failed `send_json` calls trigger automatic disconnect
- **Clean teardown** — empty connection lists are removed from the dictionary

### WebSocket Endpoint

```python
@router.websocket("/ws/{doc_id}")
async def document_websocket(websocket: WebSocket, doc_id: str):
    await manager.connect(doc_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(doc_id, websocket)
```

The endpoint accepts connections and enters a receive loop. It handles `ping` messages for keep-alive and cleanly disconnects on client close.

## Frontend: DocumentWebSocket Class

**File:** `frontend/src/lib/websocket.ts`

```typescript
export class DocumentWebSocket {
  private ws: WebSocket | null = null;
  private docId: string;
  private callbacks: Set<WSCallback> = new Set();
  private reconnectAttempts = 0;
  private maxReconnects = 5;

  constructor(docId: string) { this.docId = docId; }

  connect()                     // Opens WebSocket, sets up handlers
  subscribe(callback: WSCallback) // Registers a message listener, returns unsubscribe fn
  disconnect()                  // Closes connection, clears callbacks
  send(data: object)            // Sends JSON if connection is open
}
```

### Methods

| Method | Description |
|--------|-------------|
| `connect()` | Opens `ws://host/ws/{doc_id}`, resets reconnect counter on success, triggers reconnect on close |
| `subscribe(cb)` | Adds callback to the set, returns an unsubscribe function |
| `disconnect()` | Closes the WebSocket, nulls the reference, clears all callbacks |
| `send(data)` | JSON-stringifies and sends data if the socket is in `OPEN` state |

### Auto-Reconnect

On connection close, the client retries with **exponential backoff**:

```typescript
this.ws.onclose = () => {
  if (this.reconnectAttempts < this.maxReconnects) {
    this.reconnectAttempts++;
    setTimeout(() => this.connect(), 1000 * this.reconnectAttempts);
  }
};
```

| Attempt | Delay |
|---------|-------|
| 1 | 1 second |
| 2 | 2 seconds |
| 3 | 3 seconds |
| 4 | 4 seconds |
| 5 | 5 seconds |
| > 5 | Connection abandoned |

The counter resets to 0 on successful `onopen`.

## Message Types

Messages are JSON objects with a `type` field:

| Type | Direction | Fields | Description |
|------|-----------|--------|-------------|
| `status` | Server → Client | `status`, `total_pages?`, `pages_count?`, `pages?` | Processing stage update |
| `hitl_required` | Server → Client | — | Pages need human review |
| `error` | Server → Client | `error` | Processing error occurred |
| `ping` | Client → Server | — | Keep-alive request |
| `pong` | Server → Client | — | Keep-alive response |

### WSMessage Type

```typescript
export type WSMessage = {
  type: string;
  status?: string;
  total_pages?: number;
  pages_count?: number;
  pages?: number[];
  error?: string;
  [key: string]: unknown;
};
```

## React Hook: useDocumentWebSocket

**File:** `frontend/src/hooks/useWebSocket.ts`

Bridges the `DocumentWebSocket` class with the Zustand store:

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
    return () => { unsubscribe(); ws.disconnect(); wsRef.current = null; };
  }, [docId, setProcessingStatus, setTotalPages, setError]);

  return wsRef.current;
}
```

**Lifecycle:**
1. When `docId` becomes non-null, creates a `DocumentWebSocket` and connects
2. Subscribes a handler that dispatches to Zustand store actions
3. On cleanup (component unmount or `docId` change), unsubscribes and disconnects

## Environment Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_WS_URL` | `ws://localhost:8000` | WebSocket server base URL |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | REST API base URL (for reference) |

## Related Pages

- [Frontend Overview](./overview.md) — Architecture and state management
- [Upload Flow](./upload-flow.md) — Where WebSocket updates are consumed
- [Review Interface](./review-interface.md) — HITL review triggered by `hitl_required` message
- [Local Setup](../devops/local-setup.md) — Environment variable configuration
