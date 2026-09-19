"""Execute one agent turn: a bounded, instrumented tool-calling loop."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from agentmesh.agents.spec import AgentDefinition, AgentResult, ToolCallRecord
from agentmesh.config import Settings, get_settings
from agentmesh.events.bus import EventBus, try_get_bus
from agentmesh.llm.factory import build_chat_model
from agentmesh.logger import get_logger
from agentmesh.schemas import EventType
from agentmesh.tools import get_tools

log = get_logger("agents.runtime")

MAX_OBSERVATION_CHARS = 4_000
MAX_EVENT_CHARS = 800


def build_user_message(task: str, context: str | None = None) -> str:
    """The single user turn every agent sees. ``TASK:`` is part of the contract."""
    body = (context or "").strip() or "(nothing yet - you are contributing first)"
    return f"TASK: {task.strip()}\n\nCONTEXT FROM THE TEAM\n---------------------\n{body}\n"


def message_text(message: AnyMessage) -> str:
    """Flatten message content (str or content blocks) into plain text."""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                value = block.get("text") or block.get("content")
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(parts)
    return "" if content is None else str(content)


def stringify_tool_output(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple, int, float, bool)) or value is None:
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


class AgentRuntime:
    """Runs the think -> act -> observe loop for a single agent."""

    def __init__(
        self,
        definition: AgentDefinition,
        *,
        model: BaseChatModel | None = None,
        tools: list[BaseTool] | None = None,
        bus: EventBus | None = None,
        settings: Settings | None = None,
        max_iterations: int | None = None,
    ) -> None:
        self._definition = definition
        self._settings = settings or get_settings()
        self._bus = bus if bus is not None else try_get_bus()
        self._model = model or build_chat_model(
            definition.name, temperature=definition.temperature, settings=self._settings
        )
        self._tools = get_tools(definition.tools) if tools is None else tools
        self._tools_by_name = {tool.name: tool for tool in self._tools}
        self._max_iterations = max_iterations or min(
            definition.max_iterations, self._settings.max_tool_iterations
        )

    @property
    def definition(self) -> AgentDefinition:
        return self._definition

    async def arun(self, task: str, *, run_id: str, context: str | None = None) -> AgentResult:
        name = self._definition.name
        messages: list[AnyMessage] = [
            SystemMessage(content=self._definition.system_prompt),
            HumanMessage(content=build_user_message(task, context)),
        ]
        records: list[ToolCallRecord] = []
        iterations = 0

        await self._emit(
            run_id, EventType.AGENT_STARTED, f"{name} started", {"tools": sorted(self._tools_by_name)}
        )
        started = time.perf_counter()

        try:
            bound = self._model.bind_tools(self._tools) if self._tools else self._model
            for iteration in range(1, self._max_iterations + 1):
                iterations = iteration
                response = await asyncio.wait_for(
                    bound.ainvoke(messages), timeout=self._settings.llm_timeout_seconds
                )
                messages.append(response)
                calls = list(getattr(response, "tool_calls", None) or [])
                if not calls:
                    break
                for index, call in enumerate(calls):
                    messages.append(await self._invoke_tool(run_id, call, index, records))
            text = self._final_text(messages)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("agent failed", extra={"run_id": run_id, "agent": name, "error": str(exc)})
            duration_ms = int((time.perf_counter() - started) * 1000)
            await self._emit(
                run_id,
                EventType.AGENT_FINISHED,
                f"{name} failed: {type(exc).__name__}",
                {"ok": False, "error": str(exc), "duration_ms": duration_ms},
            )
            return AgentResult(
                agent=name,
                iterations=iterations,
                tool_calls=records,
                messages=messages,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )

        duration_ms = int((time.perf_counter() - started) * 1000)
        await self._emit(
            run_id,
            EventType.AGENT_FINISHED,
            f"{name} finished",
            {
                "ok": True,
                "iterations": iterations,
                "tool_calls": [record.name for record in records],
                "duration_ms": duration_ms,
                "preview": text[:MAX_EVENT_CHARS],
            },
        )
        return AgentResult(
            agent=name, text=text, iterations=iterations, tool_calls=records, messages=messages
        )

    # ------------------------------------------------------------------ internals
    async def _invoke_tool(
        self,
        run_id: str,
        call: dict[str, Any],
        index: int,
        records: list[ToolCallRecord],
    ) -> ToolMessage:
        name = str(call.get("name") or "unknown")
        args = call.get("args") or {}
        if not isinstance(args, dict):
            args = {"value": args}
        call_id = str(call.get("id") or f"call_{self._definition.name}_{index}")

        await self._emit(
            run_id,
            EventType.TOOL_STARTED,
            f"{self._definition.name} -> {name}",
            {"tool": name, "args": _truncate_args(args)},
        )

        tool = self._tools_by_name.get(name)
        started = time.perf_counter()
        if tool is None:
            ok, output = False, f"error: tool {name!r} is not available to this agent"
        else:
            try:
                ok, output = True, stringify_tool_output(await tool.ainvoke(args))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                ok, output = False, f"error: {type(exc).__name__}: {exc}"
        duration_ms = int((time.perf_counter() - started) * 1000)
        output = output[:MAX_OBSERVATION_CHARS]

        records.append(
            ToolCallRecord(
                name=name, args=args, ok=ok, output=output[:MAX_EVENT_CHARS], duration_ms=duration_ms
            )
        )
        await self._emit(
            run_id,
            EventType.TOOL_FINISHED,
            f"{name} {'completed' if ok else 'failed'}",
            {"tool": name, "ok": ok, "duration_ms": duration_ms, "output": output[:MAX_EVENT_CHARS]},
        )
        return ToolMessage(content=output, tool_call_id=call_id, name=name)

    def _final_text(self, messages: list[AnyMessage]) -> str:
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                text = message_text(message).strip()
                if text:
                    return text
        return f"[{self._definition.name}] produced no output."

    async def _emit(self, run_id: str, event_type: EventType, message: str, data: dict[str, Any]) -> None:
        if self._bus is None:
            return
        await self._bus.emit(run_id, event_type, agent=self._definition.name, message=message, data=data)


def _truncate_args(args: dict[str, Any], limit: int = 400) -> dict[str, Any]:
    truncated: dict[str, Any] = {}
    for key, value in args.items():
        text = value if isinstance(value, str) else stringify_tool_output(value)
        truncated[key] = text[:limit]
    return truncated


__all__ = ["AgentRuntime", "build_user_message", "message_text", "stringify_tool_output"]
