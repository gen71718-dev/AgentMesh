"""LangGraph checkpointer selection.

Memory is the default: it is enough for a single process and needs nothing
installed. Redis makes runs durable and resumable across restarts, which is what
``execution_mode=queue`` deployments want.

The Redis checkpointer indexes its checkpoints with RediSearch, so it only works
against a Redis Stack server. A plain ``redis-server`` has no ``FT.*`` commands;
when that happens AgentMesh says so once and keeps running on the in-memory
checkpointer rather than dying during startup.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from agentmesh.config import Settings, get_settings
from agentmesh.logger import get_logger

log = get_logger("graph.checkpoint")

REDIS_STACK_HINT = (
    "the Redis checkpointer indexes with RediSearch, which plain redis-server does "
    "not ship: run Redis Stack (redis/redis-stack-server) or set "
    "AGENTMESH_CHECKPOINT_BACKEND=memory"
)
SEARCH_MARKERS = ("ft.info", "ft._list", "ft.search", "redissearch", "unknown command", "no such module")


def memory_saver() -> Any:
    """Build the in-memory saver, tolerating the 0.2 -> 0.3 rename."""
    try:
        from langgraph.checkpoint.memory import InMemorySaver
    except ImportError:  # pragma: no cover - older langgraph
        from langgraph.checkpoint.memory import MemorySaver as InMemorySaver
    return InMemorySaver()


def _hint_for(exc: BaseException) -> str:
    """Explain the failure in terms of what the operator can change."""
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(marker in text for marker in SEARCH_MARKERS):
        return REDIS_STACK_HINT
    return "check AGENTMESH_REDIS_URL, and that the server is reachable"


async def _aclose(manager: Any) -> None:
    """Release a checkpointer context manager without masking the real error."""
    if not hasattr(manager, "__aexit__"):
        return
    try:
        await manager.__aexit__(None, None, None)
    except Exception as exc:  # closing must never raise
        log.debug("closing the redis checkpointer failed", extra={"error": str(exc)})


@asynccontextmanager
async def open_checkpointer(settings: Settings | None = None) -> AsyncIterator[Any]:
    """Yield the checkpointer selected by ``AGENTMESH_CHECKPOINT_BACKEND``.

    An unusable Redis checkpointer degrades to the in-memory saver with one
    warning instead of failing the process: a misconfigured checkpoint backend
    should not keep the API from answering.
    """
    settings = settings or get_settings()

    if settings.checkpoint_backend != "redis":
        yield memory_saver()
        return

    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
    except ImportError:
        log.warning(
            "checkpoint_backend=redis needs the optional dependency; using memory",
            extra={"hint": 'pip install "agentmesh[redis-checkpoint]"'},
        )
        yield memory_saver()
        return

    manager: Any = None
    saver: Any = None
    try:
        manager = AsyncRedisSaver.from_conn_string(settings.redis_url)
        saver = await manager.__aenter__() if hasattr(manager, "__aenter__") else manager
        setup = getattr(saver, "asetup", None)
        if setup is not None:
            await setup()
    except Exception as exc:  # the API must still come up
        log.warning(
            "checkpoint_backend=redis is unusable against this server; using memory",
            extra={"error": f"{type(exc).__name__}: {exc}", "hint": _hint_for(exc)},
        )
        await _aclose(manager)
        yield memory_saver()
        return

    log.info("redis checkpointer ready", extra={"url": settings.masked_redis_url})
    try:
        yield saver
    finally:
        await _aclose(manager)


__all__ = ["memory_saver", "open_checkpointer"]
