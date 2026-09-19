"""Backend selection."""

from __future__ import annotations

from agentmesh.config import Settings, get_settings
from agentmesh.logger import get_logger
from agentmesh.storage.base import QueueItem, RunStore

log = get_logger("storage")


async def create_store(settings: Settings | None = None) -> RunStore:
    """Build and start the store selected by ``AGENTMESH_STATE_BACKEND``."""
    settings = settings or get_settings()
    if settings.state_backend == "redis":
        from agentmesh.storage.redis_store import RedisStore

        store: RunStore = RedisStore(settings)
    else:
        from agentmesh.storage.memory import MemoryStore

        store = MemoryStore(settings)

    await store.startup()
    log.info("store ready", extra={"backend": store.name})
    return store


__all__ = ["QueueItem", "RunStore", "create_store"]
