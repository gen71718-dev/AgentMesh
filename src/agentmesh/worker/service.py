"""The queue consumer loop."""

from __future__ import annotations

import asyncio

from agentmesh.config import Settings, get_settings
from agentmesh.errors import RunNotFound, StoreError
from agentmesh.logger import get_logger
from agentmesh.storage.base import QueueItem, RunStore
from agentmesh.worker.executor import RunExecutor

log = get_logger("worker.service")


class WorkerService:
    """Claims runs from the queue and executes them concurrently."""

    def __init__(
        self,
        *,
        store: RunStore,
        executor: RunExecutor,
        settings: Settings | None = None,
    ) -> None:
        self._store = store
        self._executor = executor
        self._settings = settings or get_settings()
        self._semaphore = asyncio.Semaphore(self._settings.worker_concurrency)
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def in_flight(self) -> int:
        return len(self._tasks)

    async def run_once(self, *, timeout_s: float | None = None) -> bool:
        """Claim and execute a single run. Returns False when the queue was empty."""
        item = await self._store.dequeue_run(timeout_s=timeout_s or self._settings.queue_block_seconds)
        if item is None:
            return False
        await self._process(item)
        return True

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        log.info(
            "worker online",
            extra={
                "worker": self._settings.worker_name,
                "concurrency": self._settings.worker_concurrency,
                "backend": self._store.name,
            },
        )
        await self._reclaim()

        while not stop.is_set():
            try:
                item = await self._store.dequeue_run(timeout_s=self._settings.queue_block_seconds)
            except StoreError as exc:
                log.error("queue unavailable, backing off", extra={"error": str(exc)})
                await _sleep_or_stop(stop, 2.0)
                continue
            if item is None:
                await self._drain_completed()
                continue
            self._spawn(item)

        log.info("worker draining", extra={"in_flight": self.in_flight})
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        log.info("worker stopped")

    # ------------------------------------------------------------------ internals
    def _spawn(self, item: QueueItem) -> None:
        task = asyncio.create_task(self._process(item), name=f"run:{item.run_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _process(self, item: QueueItem) -> None:
        async with self._semaphore:
            try:
                await self._executor.execute(item.run_id)
            except RunNotFound:
                log.warning("dropping unknown run from the queue", extra={"run_id": item.run_id})
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("unhandled failure while executing a run", extra={"run_id": item.run_id})
            finally:
                try:
                    await self._store.ack_run(item)
                except StoreError as exc:
                    log.error("could not acknowledge a run", extra={"run_id": item.run_id, "error": str(exc)})

    async def _drain_completed(self) -> None:
        done = [task for task in self._tasks if task.done()]
        if done:
            await asyncio.gather(*done, return_exceptions=True)

    async def _reclaim(self) -> None:
        """Pick up runs whose previous worker died before acknowledging them."""
        try:
            stale = await self._store.recover_stale(
                min_idle_ms=int(self._settings.queue_reclaim_after_seconds * 1000)
            )
        except StoreError as exc:
            log.warning("could not reclaim stale queue items", extra={"error": str(exc)})
            return
        for item in stale:
            log.info("reclaiming a stale run", extra={"run_id": item.run_id})
            self._spawn(item)


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        return


__all__ = ["WorkerService"]
