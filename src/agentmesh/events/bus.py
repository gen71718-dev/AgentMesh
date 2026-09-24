"""The per-process event bus that every graph node publishes through."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agentmesh.errors import ConfigurationError, StoreError
from agentmesh.logger import get_logger
from agentmesh.schemas import EventType, RunEvent
from agentmesh.storage.base import RunStore

log = get_logger("events")


class EventBus:
    """Turns domain facts into durable :class:`RunEvent` records.

    Emission is best-effort by design: a transient storage failure must never
    abort a run that is otherwise healthy. SSE consumers additionally poll the
    run status, so a dropped event can never leave a stream hanging.
    """

    def __init__(self, store: RunStore) -> None:
        self._store = store

    @property
    def store(self) -> RunStore:
        return self._store

    async def emit(
        self,
        run_id: str,
        event_type: EventType,
        *,
        agent: str | None = None,
        message: str | None = None,
        data: dict[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> RunEvent:
        event = RunEvent(
            run_id=run_id,
            type=event_type,
            agent=agent,
            message=message,
            data=data or {},
        )
        if created_at is not None:
            event.created_at = created_at
            """best effort(尽力而为)持久化,只打warning不中断run"""
        try:
            return await self._store.append_event(event)
        except StoreError as exc:
            log.warning(
                "event dropped", extra={"run_id": run_id, "type": event_type.value, "error": str(exc)}
            )
            return event

    async def log(self, run_id: str, message: str, *, agent: str | None = None, **data: Any) -> RunEvent:
        return await self.emit(run_id, EventType.LOG, agent=agent, message=message, data=data)


_BUS: EventBus | None = None


def init_bus(store: RunStore) -> EventBus:
    """Install the process-wide bus. Called once from the app lifespan."""
    global _BUS
    _BUS = EventBus(store)
    return _BUS


def get_bus() -> EventBus:
    if _BUS is None:
        raise ConfigurationError("the event bus has not been initialised; call init_bus() during startup")
    return _BUS


def try_get_bus() -> EventBus | None:
    return _BUS


def reset_bus() -> None:
    """Drop the process-wide bus (used by tests)."""
    global _BUS
    _BUS = None


__all__ = ["EventBus", "get_bus", "init_bus", "reset_bus", "try_get_bus"]
