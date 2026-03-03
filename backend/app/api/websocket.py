"""WebSocket endpoint for real-time processing updates.

LangGraph streams workflow updates directly through this WebSocket
to the frontend -- no message broker needed.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


class ConnectionManager:
    """Manages active WebSocket connections per document."""

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


manager = ConnectionManager()


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
