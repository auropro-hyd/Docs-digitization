"""WebSocket notification adapter.

Delivers real-time updates directly via FastAPI WebSocket connections,
and tees each progress payload into the
:class:`~app.core.services.progress_cache.ProgressCache` so a polling
fallback can read the latest state when WS isn't available (corporate
proxies, captive portals, dropped connections).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.api.websocket import manager
from app.core.services.progress_cache import get_progress_cache


class WebSocketNotifyAdapter:
    async def send_update(self, channel: str, data: dict) -> None:
        # Tee progress payloads into the cache before broadcasting so
        # a poll that races with the WS push always sees a value at
        # least as fresh as the WS subscribers do. The cache filters
        # to ``type=="progress"`` internally; non-progress payloads
        # are dropped there with no extra branching here.
        get_progress_cache().set(channel, data)
        await manager.broadcast(channel, data)

    async def subscribe(self, channel: str) -> AsyncIterator[dict]:
        raise NotImplementedError("WebSocket adapter uses push model via broadcast, not pull via subscribe")
        yield  # type: ignore[misc]
