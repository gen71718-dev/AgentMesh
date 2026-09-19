"""Shared FastAPI dependencies.

Everything the routes need lives on ``app.state``, populated by the lifespan.
That keeps the routes importable (and testable) without any global wiring.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from agentmesh.config import Settings, get_settings
from agentmesh.events.bus import EventBus
from agentmesh.storage.base import RunStore
from agentmesh.worker.executor import GraphRegistry, RunExecutor


def settings_dep() -> Settings:
    return get_settings()


def store_dep(request: Request) -> RunStore:
    return request.app.state.store


def bus_dep(request: Request) -> EventBus:
    return request.app.state.bus


def executor_dep(request: Request) -> RunExecutor:
    return request.app.state.executor


def graphs_dep(request: Request) -> GraphRegistry:
    return request.app.state.graphs


SettingsDep = Annotated[Settings, Depends(settings_dep)]
StoreDep = Annotated[RunStore, Depends(store_dep)]
BusDep = Annotated[EventBus, Depends(bus_dep)]
ExecutorDep = Annotated[RunExecutor, Depends(executor_dep)]
GraphsDep = Annotated[GraphRegistry, Depends(graphs_dep)]

__all__ = [
    "BusDep",
    "ExecutorDep",
    "GraphsDep",
    "SettingsDep",
    "StoreDep",
    "bus_dep",
    "executor_dep",
    "graphs_dep",
    "settings_dep",
    "store_dep",
]

