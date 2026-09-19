"""The run lifecycle: submit, inspect, stream and cancel."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from agentmesh.agents.registry import resolve_agents
from agentmesh.api.deps import BusDep, ExecutorDep, SettingsDep, StoreDep
from agentmesh.errors import AgentNotFound, StoreError
from agentmesh.logger import get_logger
from agentmesh.schemas import (
    EventType,
    RunAccepted,
    RunEvent,
    RunListResponse,
    RunRecord,
    RunRequest,
    RunStatus,
    utcnow,
)

log = get_logger("api.runs")

router = APIRouter(prefix="/runs", tags=["runs"])

TERMINAL_EVENTS = frozenset({EventType.RUN_COMPLETED, EventType.RUN_FAILED, EventType.RUN_CANCELLED})
KEEPALIVE_MS = 15_000


@router.post(
    "",
    response_model=RunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a run",
)
async def create_run(
    payload: RunRequest,
    request: Request,
    store: StoreDep,
    bus: BusDep,
    executor: ExecutorDep,
    settings: SettingsDep,
) -> RunAccepted:
    try:
        definitions = resolve_agents(payload.agents, default=settings.default_agent_list)
    except AgentNotFound as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if not definitions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="no agents selected: pass `agents` or set AGENTMESH_DEFAULT_AGENTS",
        )

    record = RunRecord(
        task=payload.task,
        agents=[definition.name for definition in definitions],
        max_steps=payload.max_steps or settings.max_supervisor_steps,
        metadata=payload.metadata,
        queued_at=utcnow(),
    )
    await store.save_run(record)
    await bus.emit(
        record.id,
        EventType.RUN_QUEUED,
        message=f"queued for {', '.join(record.agents)}",
        data={"agents": record.agents, "mode": settings.execution_mode, "max_steps": record.max_steps},
    )

    if settings.execution_mode == "inline":
        task = asyncio.create_task(executor.execute(record.id), name=f"run:{record.id}")
        request.app.state.tasks.add(task)
        task.add_done_callback(lambda finished: _release(request, finished))
    else:
        await store.enqueue_run(record.id)

    log.info("run accepted", extra={"run_id": record.id, "agents": record.agents})
    return RunAccepted(
        run_id=record.id,
        thread_id=record.thread_id,
        status=record.status,
        created_at=record.created_at,
    )


@router.get("", response_model=RunListResponse, summary="List recent runs")
async def list_runs(
    store: StoreDep,
    settings: SettingsDep,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
) -> RunListResponse:
    items = await store.list_runs(limit=min(limit, settings.run_history_limit), status=run_status)
    return RunListResponse(items=items, total=len(items))


@router.get("/{run_id}", response_model=RunRecord, summary="Fetch one run")
async def get_run(run_id: str, store: StoreDep) -> RunRecord:
    return await _require_run(store, run_id)


@router.get("/{run_id}/result", summary="Fetch just the answer")
async def get_run_result(run_id: str, store: StoreDep) -> dict[str, Any]:
    record = await _require_run(store, run_id)
    return {
        "run_id": record.id,
        "status": record.status.value,
        "result": record.result,
        "error": record.error,
        "duration_ms": record.duration_ms,
    }


@router.get(
    "/{run_id}/events",
    response_model=list[RunEvent],
    summary="Read the event log (JSON)",
)
async def list_run_events(
    run_id: str,
    store: StoreDep,
    after: Annotated[str | None, Query(description="Return events strictly after this event id")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[RunEvent]:
    await _require_run(store, run_id)
    return await store.read_events(run_id, after=after, limit=limit)


@router.get("/{run_id}/stream", summary="Stream run events (Server-Sent Events)")
async def stream_run_events(
    run_id: str,
    request: Request,
    store: StoreDep,
    after: Annotated[str | None, Query(description="Resume after this event id")] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Replay from ``Last-Event-ID``/``after`` and then follow the run live."""
    await _require_run(store, run_id)
    cursor = after or last_event_id
    return StreamingResponse(
        _event_stream(store, run_id, cursor, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{run_id}/cancel", response_model=RunRecord, summary="Cancel a run")
async def cancel_run(run_id: str, store: StoreDep, bus: BusDep) -> RunRecord:
    record = await _require_run(store, run_id)
    if record.status.is_terminal:
        return record

    await store.request_cancel(run_id)
    if record.status is RunStatus.QUEUED:
        record.status = RunStatus.CANCELLED
        record.error = "cancelled before execution started"
        record.finished_at = utcnow()
        record.touch_duration()
        await store.save_run(record)
        await bus.emit(
            run_id,
            EventType.RUN_CANCELLED,
            message=record.error,
            data={"status": record.status.value, "duration_ms": record.duration_ms},
        )
    else:
        await bus.emit(
            run_id,
            EventType.LOG,
            message="cancellation requested; the run stops at the next supervisor step",
        )
    return record


async def _require_run(store: StoreDep, run_id: str) -> RunRecord:
    try:
        record = await store.get_run(run_id)
    except StoreError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no run with id {run_id!r}",
        )
    return record


async def _event_stream(
    store: StoreDep,
    run_id: str,
    cursor: str | None,
    request: Request,
) -> AsyncIterator[str]:
    yield "retry: 3000\n\n"
    yield ": agentmesh stream open\n\n"
    while True:
        if await request.is_disconnected():
            return
        try:
            events = await store.read_events(run_id, after=cursor, block_ms=KEEPALIVE_MS, limit=200)
        except StoreError as exc:
            yield _sse("error", {"message": str(exc)})
            return

        if not events:
            yield ": keep-alive\n\n"
        for event in events:
            cursor = event.id or cursor
            yield _format_event(event)
            if event.type in TERMINAL_EVENTS:
                yield ": stream closed\n\n"
                return

        record = await store.get_run(run_id)
        if record is not None and record.status.is_terminal:
            for event in await store.read_events(run_id, after=cursor, limit=200):
                yield _format_event(event)
            yield ": stream closed\n\n"
            return


def _format_event(event: RunEvent) -> str:
    lines = []
    if event.id:
        lines.append(f"id: {event.id}")
    lines.append(f"event: {event.type.value}")
    lines.append(f"data: {event.model_dump_json()}")
    return "\n".join(lines) + "\n\n"


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _release(request: Request, task: asyncio.Task[Any]) -> None:
    request.app.state.tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:  # pragma: no cover - defensive
        log.error("background run task raised", extra={"error": f"{type(error).__name__}: {error}"})


__all__ = ["router"]
