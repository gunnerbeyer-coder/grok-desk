"""In-process JSON bus. Payloads must be JSON-serializable dicts."""

from __future__ import annotations

import asyncio
import json


class Bus:
    def __init__(self, maxsize: int = 200):
        self.maxsize = maxsize
        self._subs: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, topic: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.maxsize)
        self._subs.setdefault(topic, []).append(queue)
        return queue

    def publish(self, topic: str, payload: dict) -> None:
        if not isinstance(payload, dict):
            raise TypeError("bus payload must be a dict")
        json.dumps(payload)
        for queue in self._subs.get(topic, []):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(payload)
