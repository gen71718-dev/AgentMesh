"""Storage contract shared by the in-memory and Redis backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from agentmesh.schemas import RunEvent, RunRecord, RunStatus


@dataclass(frozen=True, slots=True)
class QueueItem:
    """A run ready to be executed by a worker."""

    run_id: str
    token: str = ""


class RunStore(ABC):
    """Persistence + queue + event log for runs.

    Two implementations ship with AgentMesh:

    * :class:`~agentmesh.storage.memory.MemoryStore` - single process, no
      external dependencies, ideal for tests and local development.
    * :class:`~agentmesh.storage.redis.RedisStore` - shared state, a durable
      event log and a real work queue, so the API and the workers can scale
      independently.
    """

    name: str = "store"

    async def startup(self) -> None:  # pragma: no cover - trivial default
        """Open connections / create groups. Must be idempotent."""

    async def shutdown(self) -> None:  # pragma: no cover - trivial default
        """Release connections."""

    # ---------------------------------------------------------- run records
    @abstractmethod
    async def ping(self) -> bool:
        """Return True when the backend is reachable."""

    @abstractmethod
    async def save_run(self, record: RunRecord) -> None:
        """Create or overwrite a run document."""

    @abstractmethod
    async def get_run(self, run_id: str) -> RunRecord | None:
        """Fetch one run document."""

    @abstractmethod
    async def list_runs(self, limit: int = 50, status: RunStatus | None = None) -> list[RunRecord]:
        """Most recent runs first."""

    # ------------------------------------------------------------ event log
    @abstractmethod
    async def append_event(self, event: RunEvent) -> RunEvent:
        """Append to the run's event log, returning the event with `seq`/`id` set."""

    @abstractmethod
    async def read_events(
        self,
        run_id: str,
        *,
        after: str | None = None,
        block_ms: int | None = None,
        limit: int = 500,
    ) -> list[RunEvent]:
        """Read events strictly after the ``after`` cursor, optionally blocking."""

    # ----------------------------------------------------------- cancellation
    @abstractmethod
    async def request_cancel(self, run_id: str) -> bool:
        """Mark a run as cancelled. Returns True when the flag was set."""

    @abstractmethod
    async def is_cancelled(self, run_id: str) -> bool:
        """Has cancellation been requested for this run?"""

    # ----------------------------------------------------------------- queue
    @abstractmethod
    async def enqueue_run(self, run_id: str) -> None:
        """Publish a run for a worker to pick up."""

    @abstractmethod
    async def dequeue_run(self, *, timeout_s: float = 5.0) -> QueueItem | None:
        """Claim the next queued run, or None on timeout."""

    @abstractmethod
    async def ack_run(self, item: QueueItem) -> None:
        """Acknowledge a claimed item so it is not redelivered."""

    @abstractmethod
    async def queue_depth(self) -> int:
        """Number of runs waiting to be claimed."""

    @abstractmethod
    async def recover_stale(self, *, min_idle_ms: int, limit: int = 32) -> list[QueueItem]:
        """Reclaim items whose worker died before acknowledging them."""


__all__ = ["QueueItem", "RunStore"]

