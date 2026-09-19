"""Operational endpoints: liveness, readiness and metrics."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from fastapi.responses import PlainTextResponse

from agentmesh import __version__
from agentmesh.api.deps import GraphsDep, SettingsDep, StoreDep
from agentmesh.llm.factory import llm_info
from agentmesh.schemas import HealthResponse, RunStatus
from agentmesh.storage.base import RunStore

router = APIRouter(tags=["ops"])


@router.get("/healthz", response_model=HealthResponse, summary="Liveness probe")
async def healthz(settings: SettingsDep) -> HealthResponse:
    """Cheap: never touches the store, so it cannot flap during a Redis blip."""
    return HealthResponse(status="ok", version=__version__, environment=settings.environment)


@router.get("/readyz", response_model=HealthResponse, summary="Readiness probe")
async def readyz(
    response: Response,
    settings: SettingsDep,
    store: StoreDep,
    graphs: GraphsDep,
) -> HealthResponse:
    reachable = await store.ping()
    checks: dict[str, object] = {
        "store": {
            "backend": store.name,
            "reachable": reachable,
            "queue_depth": await _safe_queue_depth(store),
        },
        "llm": llm_info(settings),
        "execution_mode": settings.execution_mode,
        "compiled_teams": graphs.cached_teams,
    }
    if not reachable:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if reachable else "degraded",
        version=__version__,
        environment=settings.environment,
        checks=checks,
    )


@router.get("/metrics", response_class=PlainTextResponse, summary="Prometheus-style metrics")
async def metrics(settings: SettingsDep, store: StoreDep) -> PlainTextResponse:
    records = await store.list_runs(limit=settings.run_history_limit)
    counts = {member: 0 for member in RunStatus}
    for record in records:
        counts[record.status] += 1

    lines = [
        "# HELP agentmesh_queue_depth Runs waiting to be claimed by a worker.",
        "# TYPE agentmesh_queue_depth gauge",
        f"agentmesh_queue_depth {await _safe_queue_depth(store)}",
        "# HELP agentmesh_runs Number of retained runs by status.",
        "# TYPE agentmesh_runs gauge",
    ]
    for member, count in counts.items():
        lines.append(f'agentmesh_runs{{status="{member.value}"}} {count}')
    lines.append(f'agentmesh_info{{version="{__version__}",backend="{store.name}"}} 1')
    return PlainTextResponse("\n".join(lines) + "\n")


async def _safe_queue_depth(store: RunStore) -> int:
    try:
        return await store.queue_depth()
    except Exception:  # noqa: BLE001 - probes must never raise
        return -1


__all__ = ["router"]
