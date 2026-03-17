"""WebSocket endpoint for real-time processing updates.

LangGraph streams workflow updates directly through this WebSocket
to the frontend -- no message broker needed.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections per document."""

    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, doc_id: str, websocket: WebSocket):
        await websocket.accept()
        self._connections.setdefault(doc_id, []).append(websocket)
        count = len(self._connections[doc_id])
        logger.info(f"[WS] Connected to doc_id={doc_id} ({count} active)")

    def disconnect(self, doc_id: str, websocket: WebSocket):
        conns = self._connections.get(doc_id, [])
        if websocket in conns:
            conns.remove(websocket)
        if not conns:
            self._connections.pop(doc_id, None)
        remaining = len(self._connections.get(doc_id, []))
        logger.info(f"[WS] Disconnected from doc_id={doc_id} ({remaining} remaining)")

    async def broadcast(self, doc_id: str, data: dict):
        conns = self._connections.get(doc_id, [])
        if not conns:
            return
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(doc_id, ws)

    @property
    def active_connections(self) -> int:
        return sum(len(c) for c in self._connections.values())


manager = ConnectionManager()


@router.websocket("/ws/{doc_id}")
async def document_websocket(websocket: WebSocket, doc_id: str):
    await manager.connect(doc_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except (json.JSONDecodeError, ValueError):
                logger.warning(f"[WS] Invalid JSON from doc_id={doc_id}: {data[:100]}")
                continue
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(doc_id, websocket)
    except Exception as e:
        logger.warning(f"[WS] Error for doc_id={doc_id}: {e}")
        manager.disconnect(doc_id, websocket)
