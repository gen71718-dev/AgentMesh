"""Agent definitions, registry and runtime."""

from __future__ import annotations

# Importing this module is what registers the built-in agent team.
import agentmesh.agents.builtin  # noqa: F401
from agentmesh.agents.registry import (
    agent_names,
    all_agents,
    get_agent,
    has_agent,
    register_agent,
    resolve_agents,
)
from agentmesh.agents.runtime import AgentRuntime, build_user_message
from agentmesh.agents.spec import AgentDefinition, AgentResult, ToolCallRecord

__all__ = [
    "AgentDefinition",
    "AgentResult",
    "AgentRuntime",
    "ToolCallRecord",
    "agent_names",
    "all_agents",
    "build_user_message",
    "get_agent",
    "has_agent",
    "register_agent",
    "resolve_agents",
]
