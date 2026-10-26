from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import ClassVar

logger = logging.getLogger(__name__)


@dataclass
class ProgressEvent:
    task_id: str
    stage: str
    progress_percent: int
    status: str  # "PROCESSING" | "COMPLETED" | "FAILED"
    detail: dict | None = None
    revision: int = 0


class ProgressBus:
    """Thread-safe pub/sub bridge between worker threads and async SSE handlers."""

    _instance: ClassVar[ProgressBus | None] = None

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[ProgressEvent]]] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def get_instance(cls) -> ProgressBus:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, event: ProgressEvent) -> None:
        if self._loop is None:
            return
        with self._lock:
            queues = list(self._subscribers.get(event.task_id, []))
        for queue in queues:
            try:
                self._loop.call_soon_threadsafe(self._offer_if_subscribed, event.task_id, queue, event)
            except RuntimeError:
                pass

    async def subscribe(self, task_id: str) -> asyncio.Queue[ProgressEvent]:
        queue: asyncio.Queue[ProgressEvent] = asyncio.Queue(maxsize=1)
        with self._lock:
            self._subscribers.setdefault(task_id, []).append(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue[ProgressEvent]) -> None:
        with self._lock:
            subscribers = self._subscribers.get(task_id, [])
            if queue in subscribers:
                subscribers.remove(queue)
            if not subscribers:
                self._subscribers.pop(task_id, None)

    def _offer_if_subscribed(
        self,
        task_id: str,
        queue: asyncio.Queue[ProgressEvent],
        event: ProgressEvent,
    ) -> None:
        with self._lock:
            if queue not in self._subscribers.get(task_id, []):
                return
            self._offer_latest(queue, event)

    @staticmethod
    def _offer_latest(queue: asyncio.Queue[ProgressEvent], event: ProgressEvent) -> None:
        if queue.empty():
            queue.put_nowait(event)
            return
        pending = queue.get_nowait()
        if pending.status in {"COMPLETED", "FAILED"}:
            queue.put_nowait(pending)
            return
        queue.put_nowait(event)
