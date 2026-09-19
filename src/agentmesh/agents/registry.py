"""The agent registry."""

from __future__ import annotations

from agentmesh.agents.spec import AgentDefinition
from agentmesh.errors import AgentNotFound

_REGISTRY: dict[str, AgentDefinition] = {}


def register_agent(definition: AgentDefinition) -> AgentDefinition:
    if definition.name in _REGISTRY:
        raise ValueError(f"agent {definition.name!r} is already registered")
    _REGISTRY[definition.name] = definition
    return definition


def get_agent(name: str) -> AgentDefinition:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise AgentNotFound(
            f"unknown agent {name!r}; available: {', '.join(agent_names()) or 'none'}"
        ) from exc


def has_agent(name: str) -> bool:
    return name in _REGISTRY


def agent_names() -> list[str]:
    return [definition.name for definition in all_agents()]


def all_agents() -> list[AgentDefinition]:
    """Every registered agent, in declared order."""
    return sorted(_REGISTRY.values(), key=lambda definition: (definition.order, definition.name))


def resolve_agents(names: list[str] | None, *, default: list[str] | None = None) -> list[AgentDefinition]:
    """Resolve a list of names into definitions, preserving the caller's order."""
    selected = list(names) if names else list(default or [])
    if not selected:
        return []
    seen: set[str] = set()
    resolved: list[AgentDefinition] = []
    for name in selected:
        if name in seen:
            continue
        seen.add(name)
        resolved.append(get_agent(name))
    return resolved


def reset() -> None:
    """Forget every registered agent - only used by tests."""
    _REGISTRY.clear()


__all__ = [
    "agent_names",
    "all_agents",
    "get_agent",
    "has_agent",
    "register_agent",
    "reset",
    "resolve_agents",
]
