"""A deliberately restricted Python sandbox (opt-in)."""

from __future__ import annotations

import contextlib
import datetime
import io
import json
import math
import re
import statistics

from langchain_core.tools import tool

from agentmesh.config import get_settings
from agentmesh.tools.registry import ToolSpec, register

MAX_CODE_CHARS = 4_000
MAX_OUTPUT_CHARS = 4_000

_SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "format": format,
    "frozenset": frozenset,
    "int": int,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "pow": pow,
    "print": print,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}
_SAFE_GLOBALS: dict[str, object] = {
    "__builtins__": _SAFE_BUILTINS,
    "math": math,
    "json": json,
    "re": re,
    "statistics": statistics,
    "datetime": datetime,
}


def _availability() -> tuple[bool, str]:
    if get_settings().enable_python_tool:
        return True, ""
    return False, "set AGENTMESH_ENABLE_PYTHON_TOOL=true to allow code execution"


def run_snippet(code: str) -> str:
    """Execute ``code`` with restricted builtins and return captured stdout."""
    source = (code or "").strip()
    if not source:
        return "python_repl error: no code provided"
    if len(source) > MAX_CODE_CHARS:
        return f"python_repl error: code exceeds {MAX_CODE_CHARS} characters"

    buffer = io.StringIO()
    namespace: dict[str, object] = {}
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            exec(compile(source, "<agentmesh-repl>", "exec"), dict(_SAFE_GLOBALS), namespace)  # noqa: S102
    except Exception as exc:  # noqa: BLE001 - surface every failure to the model
        captured = buffer.getvalue()
        return f"python_repl raised {type(exc).__name__}: {exc}\n{captured}"[:MAX_OUTPUT_CHARS]

    output = buffer.getvalue()
    if not output.strip():
        interesting = {k: v for k, v in namespace.items() if not k.startswith("_")}
        output = f"(no output; final variables: {interesting})"
    return output[:MAX_OUTPUT_CHARS]


@tool("python_repl")
def python_repl(code: str) -> str:
    """Execute Python in a restricted sandbox and return whatever it printed.

    Only a small set of builtins plus math/json/re/statistics/datetime are
    available, and imports are disabled. Always ``print`` the value you want
    to observe. Disabled unless AGENTMESH_ENABLE_PYTHON_TOOL is true.
    """
    available, reason = _availability()
    if not available:
        return f"python_repl is disabled ({reason})"
    return run_snippet(code)


register(
    ToolSpec(
        name="python_repl",
        description=python_repl.description,
        factory=lambda: python_repl,
        tags=("code", "sandboxed", "opt-in"),
        availability=_availability,
    )
)

__all__ = ["python_repl", "run_snippet"]
