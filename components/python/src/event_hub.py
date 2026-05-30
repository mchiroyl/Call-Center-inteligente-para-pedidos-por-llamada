"""Realtime event hub used by operations dashboards."""

import asyncio
from typing import Any

from fastapi import WebSocket


class KitchenEventHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self) -> None:
        self._loop = asyncio.get_running_loop()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    async def publish(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients)

        stale: list[WebSocket] = []
        for client in clients:
            try:
                await client.send_json(payload)
            except RuntimeError:
                stale.append(client)

        if stale:
            async with self._lock:
                for client in stale:
                    self._clients.discard(client)

    def publish_from_thread(self, payload: dict[str, Any]) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self.publish(payload))
            )


kitchen_events = KitchenEventHub()

