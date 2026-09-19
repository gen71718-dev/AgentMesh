"""Turn a queued run into graph execution and a terminal run record."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from agentmesh.agents.registry import resolve_agents
from agentmesh.config import Settings, get_settings
from agentmesh.errors import RunCancelled, RunNotFound
from agentmesh.events.bus import EventBus, init_bus, try_get_bus
from agentmesh.graph.builder import build_graph
from agentmesh.graph.state import initial_state
from agentmesh.logger import get_logger
from agentmesh.schemas import EventType, RunRecord, RunStatus, utcnow
from agentmesh.storage.base import RunStore

log = get_logger("worker.executor")


class GraphRegistry:
    """Compile one graph per distinct agent team (compilation is not free)."""

    def __init__(self, settings: Settings, checkpointer: Any | None = None) -> None:
        self._settings = settings
        self._checkpointer = checkpointer
        self._graphs: dict[tuple[str, ...], Any] = {}
        self._lock = asyncio.Lock()

    async def get(self, agents: Sequence[str]) -> Any:
        key = tuple(agents)
        async with self._lock:
            graph = self._graphs.get(key)
            if graph is None:
                definitions = resolve_agents(list(key))
                graph = build_graph(definitions, settings=self._settings, checkpointer=self._checkpointer)
                self._graphs[key] = graph
            return graph

    @property
    def cached_teams(self) -> list[list[str]]:
        """Teams that currently have a compiled graph (used by /readyz)."""
        return [list(key) for key in self._graphs]


class RunExecutor:
    """Executes exactly one run, and records the outcome whatever happens."""

    def __init__(
        self,
        *,
        store: RunStore,
        graphs: GraphRegistry,
        bus: EventBus | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._store = store
        self._graphs = graphs
        self._settings = settings or get_settings()
        self._bus = bus if bus is not None else init_bus(store)
        if try_get_bus() is None:
            # The graph nodes resolve the bus from process state; make sure it is
            # installed even when a caller injected its own.
            init_bus(store)

    @property
    def bus(self) -> EventBus:
        return self._bus

    async def execute(self, run_id: str) -> RunRecord:
        record = await self._store.get_run(run_id)
        if record is None:
            raise RunNotFound(f"no run with id {run_id!r}")
        if record.status.is_terminal:
            log.info("run already finished", extra={"run_id": run_id, "status": record.status.value})
            return record
        if await self._store.is_cancelled(run_id):
            return await self._finish(record, RunStatus.CANCELLED, error="cancelled before execution started")

        record.status = RunStatus.RUNNING
        record.started_at = utcnow()
        record.worker = self._settings.worker_name
        record.attempts += 1
        await self._store.save_run(record)
        await self._bus.emit(
            run_id,
            EventType.RUN_STARTED,
            message=f"run started on {record.worker}",
            data={"agents": record.agents, "worker": record.worker, "attempt": record.attempts},
        )

        try:
            graph = await self._graphs.get(record.agents)
            final = await asyncio.wait_for(
                graph.ainvoke(
                    initial_state(
                        run_id=run_id,
                        task=record.task,
                        candidates=record.agents,
                        max_steps=record.max_steps,
                    ),
                    {
                        "configurable": {"thread_id": record.thread_id},
                        "recursion_limit": max(25, record.max_steps * 3 + 5),
                    },
                ),
                timeout=self._settings.run_timeout_seconds,
            )
        except RunCancelled:
            return await self._finish(record, RunStatus.CANCELLED, error="cancelled by request")
        except TimeoutError:
            return await self._finish(
                record,
                RunStatus.FAILED,
                error=f"the run exceeded its {self._settings.run_timeout_seconds:.0f}s budget",
            )
        except asyncio.CancelledError:
            await self._finish(record, RunStatus.FAILED, error="the worker was shut down mid-run")
            raise
        except Exception as exc:
            log.exception("run failed", extra={"run_id": run_id})
            return await self._finish(record, RunStatus.FAILED, error=f"{type(exc).__name__}: {exc}")

        answer = str((final or {}).get("final_answer") or "").strip()
        return await self._finish(
            record,
            RunStatus.SUCCEEDED,
            result=answer or "The run completed but produced no answer.",
            data={
                "trace": (final or {}).get("trace") or [],
                "results": list((final or {}).get("results") or {}),
            },
        )

    async def _finish(
        self,
        record: RunRecord,
        status: RunStatus,
        *,
        result: str | None = None,
        error: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> RunRecord:
        record.status = status
        record.finished_at = utcnow()
        record.touch_duration()
        if result is not None:
            record.result = result
        if error is not None:
            record.error = error
        await self._store.save_run(record)

        event_type = {
            RunStatus.SUCCEEDED: EventType.RUN_COMPLETED,
            RunStatus.FAILED: EventType.RUN_FAILED,
            RunStatus.CANCELLED: EventType.RUN_CANCELLED,
        }.get(status, EventType.LOG)
        payload = {"status": status.value, "duration_ms": record.duration_ms, **(data or {})}
        if error:
            payload["error"] = error
        await self._bus.emit(record.id, event_type, message=error or "run completed", data=payload)
        log.info(
            "run finished",
            extra={"run_id": record.id, "status": status.value, "duration_ms": record.duration_ms},
        )
        return record


__all__ = ["GraphRegistry", "RunExecutor"]
