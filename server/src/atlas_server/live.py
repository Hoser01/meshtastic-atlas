"""In-process fanout for API ingestion to live SSE clients."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class LiveBroker:
    def __init__(self, queue_size: int = 256) -> None:
        self.queue_size = queue_size
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.lock = asyncio.Lock()

    async def publish(self, event: dict[str, Any]) -> None:
        async with self.lock:
            subscribers = tuple(self.subscribers)
        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(self.queue_size)
        async with self.lock:
            self.subscribers.add(queue)
        try:
            yield queue
        finally:
            async with self.lock:
                self.subscribers.discard(queue)
