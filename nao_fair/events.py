"""Monotonic event numbers and the per-socket queues behind /api/events."""

from __future__ import annotations

import asyncio
import threading
from typing import Any


class EventHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.sequence = 0
        self.loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    def make(self, event_type: str, **payload: Any) -> dict:
        """Allocate a sequence number without broadcasting. Used for a picture."""
        with self._lock:
            self.sequence += 1
            return {"type": event_type, "sequence": self.sequence, **payload}

    def emit(self, event_type: str, **payload: Any) -> dict:
        with self._lock:
            self.sequence += 1
            event = {"type": event_type, "sequence": self.sequence, **payload}
            queues = list(self._subscribers)
            loop = self.loop
        delivered = dict(event)
        for queue in queues:
            self._put(queue, delivered, loop)
        return event

    def _put(self, queue: asyncio.Queue, event: dict, loop: asyncio.AbstractEventLoop | None) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and running is loop:
            queue.put_nowait(dict(event))
            return
        if loop is not None and loop.is_running():
            delivered = threading.Event()

            def _enqueue() -> None:
                try:
                    queue.put_nowait(dict(event))
                finally:
                    delivered.set()

            loop.call_soon_threadsafe(_enqueue)
            if not delivered.wait(timeout=5):
                raise TimeoutError("event delivery timed out")
            return
        queue.put_nowait(dict(event))
