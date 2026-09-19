"""LangGraph checkpointer selection.

Memory is the default: it is enough for a single process and needs nothing
installed. Redis makes runs durable and resumable across restarts, which is what
``execution_mode=queue`` deployments want.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from agentmesh.config import Settings, get_settings
from agentmesh.logger import get_logger

log = get_logger("graph.checkpoint")


def memory_saver() -> Any:
    """Build the in-memory saver, tolerating the 0.2 -> 0.3 rename."""
    try:
        from langgraph.checkpoint.memory import InMemorySaver
    except ImportError:  # pragma: no cover - older langgraph
        from langgraph.checkpoint.memory import MemorySaver as InMemorySaver
    return InMemorySaver()


@asynccontextmanager
async def open_checkpointer(settings: Settings | None = None) -> AsyncIterator[Any]:
    """Yield the checkpointer selected by ``AGENTMESH_CHECKPOINT_BACKEND``."""
    settings = settings or get_settings()

    if settings.checkpoint_backend != "redis":
        yield memory_saver()
        return

    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
    except ImportError:
        log.warning(
            "checkpoint_backend=redis needs the optional dependency; falling back to memory",
            extra={"hint": 'pip install "agentmesh[redis-checkpoint]"'},
        )
        yield memory_saver()
        return

    manager = AsyncRedisSaver.from_conn_string(settings.redis_url)
    saver = await manager.__aenter__() if hasattr(manager, "__aenter__") else manager
    try:
        setup = getattr(saver, "asetup", None)
        if setup is not None:
            try:
                await setup()
            except Exception as exc:  # noqa: BLE001 - already-configured indexes are fine
                log.debug("checkpointer asetup() was not needed", extra={"error": str(exc)})
        log.info("redis checkpointer ready", extra={"url": settings.masked_redis_url})
        yield saver
    finally:
        if hasattr(manager, "__aexit__"):
            await manager.__aexit__(None, None, None)


__all__ = ["memory_saver", "open_checkpointer"]

