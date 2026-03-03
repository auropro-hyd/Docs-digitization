"""WebSocket notification adapter.

Delivers real-time updates directly via FastAPI WebSocket connections.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.api.websocket import manager


class WebSocketNotifyAdapter:
    async def send_update(self, channel: str, data: dict) -> None:
        await manager.broadcast(channel, data)

    async def subscribe(self, channel: str) -> AsyncIterator[dict]:
        raise NotImplementedError("WebSocket adapter uses push model via broadcast, not pull via subscribe")
        yield  # type: ignore[misc]
