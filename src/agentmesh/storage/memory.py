"""In-process store: correct, dependency-free, single process only."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque

from agentmesh.config import Settings, get_settings
from agentmesh.schemas import RunEvent, RunRecord, RunStatus
from agentmesh.storage.base import QueueItem, RunStore


class MemoryStore(RunStore):
    """Dict-backed :class:`RunStore` with a real asyncio queue and pub/sub.

    Used by the default ``AGENTMESH_STATE_BACKEND=memory`` configuration. It is
    process-local: the API and the workers must run in the same process, which
    is exactly what ``execution_mode=inline`` does.
    """

    name = "memory"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._runs: OrderedDict[str, RunRecord] = OrderedDict()
        self._events: dict[str, list[RunEvent]] = {}
        self._seq: dict[str, int] = {}
        self._stream_signals: dict[str, asyncio.Event] = {}
        self._cancelled: set[str] = set()
        self._queue: deque[str] = deque()
        self._queue_signal = asyncio.Event()
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------- run records
    async def ping(self) -> bool:
        return True

    async def save_run(self, record: RunRecord) -> None:
        async with self._lock:
            self._runs[record.id] = record.model_copy(deep=True)
            while len(self._runs) > self._settings.run_history_limit:
                _, evicted = self._runs.popitem(last=False)
                self._events.pop(evicted.id, None)
                self._seq.pop(evicted.id, None)
                self._cancelled.discard(evicted.id)

    async def get_run(self, run_id: str) -> RunRecord | None:
        async with self._lock:
            record = self._runs.get(run_id)
            return record.model_copy(deep=True) if record else None

    async def list_runs(self, limit: int = 50, status: RunStatus | None = None) -> list[RunRecord]:
        async with self._lock:
            records = [record.model_copy(deep=True) for record in reversed(self._runs.values())]
        if status is not None:
            records = [record for record in records if record.status is status]
        return records[:limit]

    # ------------------------------------------------------------ event log
    async def append_event(self, event: RunEvent) -> RunEvent:
        async with self._lock:
            seq = self._seq.get(event.run_id, 0) + 1
            self._seq[event.run_id] = seq
            event.seq = seq
            event.id = str(seq)
            log = self._events.setdefault(event.run_id, [])
            log.append(event.model_copy(deep=True))
            overflow = len(log) - self._settings.redis_stream_maxlen
            if overflow > 0:
                del log[:overflow]
            record = self._runs.get(event.run_id)
            if record is not None:
                record.events_count = len(log)
            signal = self._stream_signals.get(event.run_id)
        if signal is not None:
            signal.set()
        return event

    async def read_events(
        self,
        run_id: str,
        *,
        after: str | None = None,
        block_ms: int | None = None,
        limit: int = 500,
    ) -> list[RunEvent]:
        deadline = None if block_ms is None else time.monotonic() + block_ms / 1000
        while True:
            # Register interest *before* reading: an append that lands after the
            # clear is either picked up by the read below, or leaves the signal
            # set so the wait returns immediately. No lost wake-ups.
            signal = self._stream_signals.setdefault(run_id, asyncio.Event())
            signal.clear()
            async with self._lock:
                events = [event.model_copy(deep=True) for event in self._events.get(run_id, [])]
            if after:
                events = [event for event in events if (event.seq or 0) > int(after)]
            if events or block_ms is None:
                return events[:limit]
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return []
            try:
                await asyncio.wait_for(signal.wait(), timeout=remaining)
            except (TimeoutError, asyncio.TimeoutError):
                return []

    # ---------------------------------------------------------- cancellation
    async def request_cancel(self, run_id: str) -> bool:
        async with self._lock:
            if run_id not in self._runs:
                return False
            self._cancelled.add(run_id)
            return True

    async def is_cancelled(self, run_id: str) -> bool:
        async with self._lock:
            return run_id in self._cancelled

    # ----------------------------------------------------------------- queue
    async def enqueue_run(self, run_id: str) -> None:
        async with self._lock:
            self._queue.append(run_id)
        self._queue_signal.set()

    async def dequeue_run(self, *, timeout_s: float = 5.0) -> QueueItem | None:
        try:
            await asyncio.wait_for(self._queue_signal.wait(), timeout=timeout_s)
        except (TimeoutError, asyncio.TimeoutError):
            return None
        async with self._lock:
            if not self._queue:
                self._queue_signal.clear()
                return None
            run_id = self._queue.popleft()
            if not self._queue:
                self._queue_signal.clear()
            return QueueItem(run_id=run_id, token=run_id)

    async def ack_run(self, item: QueueItem) -> None:
        return None

    async def queue_depth(self) -> int:
        async with self._lock:
            return len(self._queue)

    async def recover_stale(self, *, min_idle_ms: int, limit: int = 32) -> list[QueueItem]:
        return []


__all__ = ["MemoryStore"]
