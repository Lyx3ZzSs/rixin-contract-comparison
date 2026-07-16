from __future__ import annotations

import asyncio

from app.services.progress_bus import ProgressBus, ProgressEvent


def event(status: str, progress: int) -> ProgressEvent:
    return ProgressEvent(
        task_id="TPROGRESS_STICKY",
        stage=status.lower(),
        progress_percent=progress,
        status=status,
    )


def test_subscriber_queue_has_capacity_one_and_latest_progress_replaces_pending() -> None:
    async def exercise() -> None:
        bus = ProgressBus()
        bus.bind_loop(asyncio.get_running_loop())
        queue = await bus.subscribe("TPROGRESS_STICKY")

        bus.publish(event("PROCESSING", 10))
        bus.publish(event("PROCESSING", 20))
        await asyncio.sleep(0)

        assert queue.maxsize == 1
        assert (await queue.get()).progress_percent == 20
        assert queue.empty()

    asyncio.run(exercise())


def test_terminal_replaces_pending_progress_and_is_never_replaced_or_removed() -> None:
    async def exercise() -> None:
        bus = ProgressBus()
        bus.bind_loop(asyncio.get_running_loop())
        queue = await bus.subscribe("TPROGRESS_STICKY")

        bus.publish(event("PROCESSING", 40))
        bus.publish(event("COMPLETED", 100))
        bus.publish(event("PROCESSING", 90))
        bus.publish(event("FAILED", 100))
        await asyncio.sleep(0)

        queued = await queue.get()
        assert queued.status == "COMPLETED"
        assert queue.empty()

    asyncio.run(exercise())
