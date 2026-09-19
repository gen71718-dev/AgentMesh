"""Tool registry plus the built-in tool set.

Importing this package registers every built-in tool.
"""

from __future__ import annotations

# Importing these modules is what registers the built-in tools.
import agentmesh.tools.calculator  # noqa: F401
import agentmesh.tools.clock  # noqa: F401
import agentmesh.tools.knowledge  # noqa: F401
import agentmesh.tools.python_repl  # noqa: F401
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
