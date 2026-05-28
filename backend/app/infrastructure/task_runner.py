from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Future
from typing import TypeVar

T = TypeVar("T")


class BackgroundTaskRunner:
    """Thin adapter around asyncio's default executor.

    Keeping this behind an object makes it straightforward to swap in a queue
    worker without changing API handlers.
    """

    def submit(self, func: Callable[[], T]) -> Future[T]:
        loop = asyncio.get_running_loop()
        return loop.run_in_executor(None, func)


default_task_runner = BackgroundTaskRunner()
