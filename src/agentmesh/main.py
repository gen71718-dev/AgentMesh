"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agentmesh import __version__
from agentmesh.api.routes import agents as agents_routes
from agentmesh.api.routes import health as health_routes
from agentmesh.api.routes import runs as runs_routes
from agentmesh.config import Settings, get_settings
from agentmesh.errors import AgentMeshError, AgentNotFound, ConfigurationError, RunNotFound, StoreError
from agentmesh.events.bus import init_bus
from agentmesh.graph.checkpointer import open_checkpointer
from agentmesh.logger import configure_logging, get_logger
from agentmesh.storage.factory import create_store
from agentmesh.worker.executor import GraphRegistry, RunExecutor

log = get_logger("main")

DESCRIPTION = """
A multi-agent orchestration service.

Submit an objective to `POST /api/v1/runs` and either poll
`GET /api/v1/runs/{run_id}` or follow `GET /api/v1/runs/{run_id}/stream` for a
live Server-Sent Events feed. A LangGraph supervisor routes work between
specialist agents until the objective is satisfied.
""".strip()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    store = await create_store(settings)
    bus = init_bus(store)
    app.state.store = store
    app.state.bus = bus

    async with open_checkpointer(settings) as checkpointer:
        graphs = GraphRegistry(settings, checkpointer)
        app.state.graphs = graphs
        app.state.executor = RunExecutor(store=store, graphs=graphs, bus=bus, settings=settings)
        app.state.tasks = set()
        app.state.started_at = time.monotonic()
        log.info(
            "agentmesh ready",
            extra={
                "version": __version__,
                "environment": settings.environment,
                "store": store.name,
                "checkpointer": settings.checkpoint_backend,
                "execution_mode": settings.execution_mode,
                "llm_provider": settings.llm_provider,
                "api_prefix": settings.api_prefix,
            },
        )
        try:
            yield
        finally:
            tasks = list(app.state.tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await store.shutdown()
            log.info("agentmesh stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application. Safe to call many times (tests, workers)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json, service="agentmesh")
    for problem in settings.validate_runtime():
        log.warning("configuration problem", extra={"problem": problem})

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings

    origins = settings.cors_origin_list
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials="*" not in origins,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID", "X-Process-Time-Ms"],
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
        return response

    _register_error_handlers(app)

    app.include_router(health_routes.router)
    app.include_router(agents_routes.router, prefix=settings.api_prefix)
    app.include_router(runs_routes.router, prefix=settings.api_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": __version__,
            "environment": settings.environment,
            "docs": "/docs",
            "health": "/healthz",
            "api": settings.api_prefix,
        }

    return app


def _register_error_handlers(app: FastAPI) -> None:
    def handler(exc_type: type[Exception], code: int):
        async def _handle(request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(status_code=code, content={"detail": str(exc), "error": exc_type.__name__})

        return _handle

    app.add_exception_handler(RunNotFound, handler(RunNotFound, status.HTTP_404_NOT_FOUND))
    app.add_exception_handler(AgentNotFound, handler(AgentNotFound, status.HTTP_404_NOT_FOUND))
    app.add_exception_handler(
        ConfigurationError, handler(ConfigurationError, status.HTTP_500_INTERNAL_SERVER_ERROR)
    )
    app.add_exception_handler(StoreError, handler(StoreError, status.HTTP_503_SERVICE_UNAVAILABLE))
    app.add_exception_handler(AgentMeshError, handler(AgentMeshError, status.HTTP_500_INTERNAL_SERVER_ERROR))


app = create_app()

__all__ = ["app", "create_app", "lifespan"]
