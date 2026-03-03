"""PostgreSQL LISTEN/NOTIFY notification adapter.

Lightweight pub/sub for multi-worker deployments using PostgreSQL's
built-in notification mechanism. Zero additional infrastructure.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import asyncpg

from app.config.settings import get_settings


class PGListenNotifyAdapter:
    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or get_settings().database.sync_url
        self._conn: asyncpg.Connection | None = None

    async def _get_connection(self) -> asyncpg.Connection:
        if self._conn is None or self._conn.is_closed():
            self._conn = await asyncpg.connect(self._dsn)
        return self._conn

    async def send_update(self, channel: str, data: dict) -> None:
        conn = await self._get_connection()
        payload = json.dumps(data)
        await conn.execute(f"NOTIFY {channel}, $1", payload)

    async def subscribe(self, channel: str) -> AsyncIterator[dict]:
        conn = await self._get_connection()
        queue: asyncio.Queue[dict] = asyncio.Queue()

        def _callback(conn, pid, channel, payload):
            queue.put_nowait(json.loads(payload))

        await conn.add_listener(channel, _callback)
        try:
            while True:
                data = await queue.get()
                yield data
        finally:
            await conn.remove_listener(channel, _callback)
