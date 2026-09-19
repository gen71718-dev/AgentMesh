"""Timezone-aware clock tool."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.tools import tool

from agentmesh.tools.registry import ToolSpec, register


@tool("current_time")
def current_time(timezone_name: str = "UTC") -> str:
    """Return the current date and time for an IANA timezone.

    ``timezone_name`` must be an IANA name such as ``UTC``, ``Asia/Shanghai``
    or ``America/New_York``. Use this whenever the answer depends on "now",
    and never guess the current date.
    """
    name = (timezone_name or "UTC").strip() or "UTC"
    try:
        tz = UTC if name.upper() == "UTC" else ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return f"unknown timezone {name!r}; use an IANA name such as 'Asia/Shanghai'"
    now = datetime.now(tz)
    return f"{now.isoformat(timespec='seconds')} ({now.strftime('%A, %d %B %Y %H:%M:%S %Z')})"


register(
    ToolSpec(
        name="current_time",
        description=current_time.description,
        factory=lambda: current_time,
        tags=("time", "deterministic"),
    )
)

__all__ = ["current_time"]
