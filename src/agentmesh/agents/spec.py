"""Agent data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AnyMessage
from pydantic import BaseModel, Field

from agentmesh.schemas import AgentInfo


class AgentDefinition(BaseModel):
    """Declarative description of one specialist agent."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    title: str
    description: str = Field(description="One line used by the supervisor to route work.")
    system_prompt: str
    tools: list[str] = Field(default_factory=list)
    temperature: float | None = None
    max_iterations: int = Field(default=3, ge=1, le=20)
    order: int = 100

    def to_info(self) -> AgentInfo:
        return AgentInfo(
            name=self.name,
            title=self.title,
            description=self.description,
            tools=list(self.tools),
            max_iterations=self.max_iterations,
        )


class ToolCallRecord(BaseModel):
    """Outcome of a single tool invocation, surfaced in events and results."""

    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    output: str = ""
    duration_ms: int = 0


@dataclass(slots=True)
class AgentResult:
    """What an agent produced for one turn."""

    agent: str
    text: str = ""
    iterations: int = 0
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    messages: list[AnyMessage] = field(default_factory=list)
    ok: bool = True
    error: str | None = None


__all__ = ["AgentDefinition", "AgentResult", "ToolCallRecord"]
