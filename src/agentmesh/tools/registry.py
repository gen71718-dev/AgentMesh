"""Tool registry: lazy construction + availability reporting."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool

from agentmesh.errors import ToolNotFound
from agentmesh.logger import get_logger
from agentmesh.schemas import ToolInfo

log = get_logger("tools")

Availability = Callable[[], "tuple[bool, str]"]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Everything needed to describe and lazily build a tool."""

    name: str
    description: str
    factory: Callable[[], BaseTool]
    tags: tuple[str, ...] = field(default_factory=tuple)
    availability: Availability | None = None

    def check(self) -> tuple[bool, str]:
        if self.availability is None:
            return True, ""
        return self.availability()


_REGISTRY: dict[str, ToolSpec] = {}
_INSTANCES: dict[str, BaseTool] = {}


def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in _REGISTRY:
        raise ValueError(f"tool {spec.name!r} is already registered")
    _REGISTRY[spec.name] = spec
    return spec


def tool(
    name: str,
    description: str,
    *,
    tags: tuple[str, ...] = (),
    availability: Availability | None = None,
) -> Callable[[Callable[[], BaseTool]], Callable[[], BaseTool]]:
    """Decorator that registers a zero-argument factory as a tool."""

    def decorator(factory: Callable[[], BaseTool]) -> Callable[[], BaseTool]:
        register(
            ToolSpec(
                name=name, description=description, factory=factory, tags=tags, availability=availability
            )
        )
        return factory

    return decorator


def has_tool(name: str) -> bool:
    return name in _REGISTRY


def tool_names() -> list[str]:
    return sorted(_REGISTRY)


def get_tool(name: str) -> BaseTool:
    """Build (once) and return the tool registered under ``name``."""
    if name not in _REGISTRY:
        raise ToolNotFound(f"unknown tool {name!r}; available: {', '.join(tool_names()) or 'none'}")
    if name not in _INSTANCES:
        _INSTANCES[name] = _REGISTRY[name].factory()
    return _INSTANCES[name]


def get_tools(names: list[str] | tuple[str, ...] | None) -> list[BaseTool]:
    """Resolve a list of tool names, warning about (and skipping) unknown ones."""
    if not names:
        return []
    resolved: list[BaseTool] = []
    for name in names:
        try:
            resolved.append(get_tool(name))
        except ToolNotFound:
            log.warning("skipping unknown tool", extra={"tool": name})
    return resolved


def list_tools() -> list[ToolInfo]:
    infos: list[ToolInfo] = []
    for name in tool_names():
        spec = _REGISTRY[name]
        available, _reason = spec.check()
        infos.append(
            ToolInfo(name=spec.name, description=spec.description, tags=list(spec.tags), available=available)
        )
    return infos


def reset() -> None:
    """Drop registered tools - only used by tests."""
    _REGISTRY.clear()
    _INSTANCES.clear()


__all__ = [
    "ToolSpec",
    "get_tool",
    "get_tools",
    "has_tool",
    "list_tools",
    "register",
    "reset",
    "tool",
    "tool_names",
]
