"""Notification port definition.

Adapters (WebSocket, PostgreSQL LISTEN/NOTIFY) must implement this protocol
for real-time event delivery to the frontend.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class NotificationPort(Protocol):
    """Port for real-time notifications to clients."""

    async def send_update(self, channel: str, data: dict) -> None:
        """Push an update to a named channel."""
        ...

    async def subscribe(self, channel: str) -> AsyncIterator[dict]:
        """Subscribe to updates on a named channel."""
        ...
