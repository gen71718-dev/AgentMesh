"""Tool registry plus the built-in tool set.

Importing this package registers every built-in tool.
"""

from __future__ import annotations

# Importing these modules is what registers the built-in tools.
import agentmesh.tools.calculator
import agentmesh.tools.clock
import agentmesh.tools.knowledge
import agentmesh.tools.python_repl
import agentmesh.tools.search  # noqa: F401
from agentmesh.tools.registry import (
    ToolSpec,
    get_tool,
    get_tools,
    has_tool,
    list_tools,
    register,
    tool,
    tool_names,
)

__all__ = [
    "ToolSpec",
    "get_tool",
    "get_tools",
    "has_tool",
    "list_tools",
    "register",
    "tool",
    "tool_names",
]
