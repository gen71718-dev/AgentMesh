"""Wire + storage models shared by the API, the graph and the workers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    """Timezone-aware ``now`` - never use naive datetimes in this codebase."""
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATUSES


_TERMINAL_STATUSES = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED})


class EventType(StrEnum):
    RUN_QUEUED = "run.queued"
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    SUPERVISOR_ROUTE = "supervisor.route"
    AGENT_STARTED = "agent.started"
    AGENT_FINISHED = "agent.finished"
    TOOL_STARTED = "tool.started"
    TOOL_FINISHED = "tool.finished"
    LOG = "log"


class RunEvent(BaseModel):
    """One entry in a run's append-only event log."""

    id: str | None = Field(default=None, description="Store-assigned cursor, used by SSE resume.")
    run_id: str
    seq: int = 0
    type: EventType
    agent: str | None = None
    message: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class RunRequest(BaseModel):
    """Body of ``POST /api/v1/runs``."""

    task: str = Field(min_length=1, max_length=20_000, description="The objective for the agent team.")
    agents: list[str] | None = Field(
        default=None,
        description="Subset of worker agents to enable. Defaults to AGENTMESH_DEFAULT_AGENTS.",
    )
    max_steps: int | None = Field(default=None, ge=1, le=50)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task")
    @classmethod
    def _strip_task(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("task must not be blank")
        return stripped


class RunRecord(BaseModel):
    """Durable run document."""

    id: str = Field(default_factory=lambda: new_id("run"))
    thread_id: str = Field(default_factory=lambda: new_id("thread"))
    status: RunStatus = RunStatus.QUEUED
    task: str
    agents: list[str] = Field(default_factory=list)
    max_steps: int = 8
    metadata: dict[str, Any] = Field(default_factory=dict)

    result: str | None = None
    error: str | None = None
    worker: str | None = None
    attempts: int = 0
    events_count: int = 0

    created_at: datetime = Field(default_factory=utcnow)
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None

    def touch_duration(self) -> None:
        if self.started_at and self.finished_at:
            self.duration_ms = int((self.finished_at - self.started_at).total_seconds() * 1000)


class RunAccepted(BaseModel):
    run_id: str
    thread_id: str
    status: RunStatus
    created_at: datetime


class RunListResponse(BaseModel):
    items: list[RunRecord]
    total: int


class AgentInfo(BaseModel):
    name: str
    title: str
    description: str
    tools: list[str] = Field(default_factory=list)
    max_iterations: int = 3


class ToolInfo(BaseModel):
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    available: bool = True


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    checks: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AgentInfo",
    "EventType",
    "HealthResponse",
    "RunAccepted",
    "RunEvent",
    "RunListResponse",
    "RunRecord",
    "RunRequest",
    "RunStatus",
    "ToolInfo",
    "new_id",
    "utcnow",
]
