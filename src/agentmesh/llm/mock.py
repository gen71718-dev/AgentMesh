"""A deterministic, dependency-free chat model.

``MockChatModel`` exists so that AgentMesh can be cloned, tested and demoed
with **no API key and no network access**. It follows a small deterministic
policy:

* bound tools + no tool observation yet  -> request the first tool
* tool observations present              -> return the final answer
* no tools bound (planner/supervisor)    -> emit ``NEXT: <agent>`` or ``NEXT: FINISH``

It also accepts an explicit ``script`` of canned replies, which unit tests use
to force specific graph paths.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr

_TASK_RE = re.compile(r"^TASK:\s*(?P<task>.+)$", re.MULTILINE)
_CANDIDATES_RE = re.compile(r"^CANDIDATES:\s*(?P<value>.*)$", re.MULTILINE)
_COMPLETED_RE = re.compile(r"^COMPLETED:\s*(?P<value>.*)$", re.MULTILINE)
_NONE_MARKERS = {"-", "", "(none)", "none"}


def _as_text(content: Any) -> str:
    """Flatten LangChain message content (str | list of blocks) into text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return "" if content is None else str(content)


class MockChatModel(BaseChatModel):
    """Offline, deterministic stand-in for a real chat model."""

    model_name: str = "mock-1"
    persona: str = "assistant"
    script: list[str] = Field(default_factory=list)
    call_first_tool: bool = True

    _cursor: int = PrivateAttr(default=0)

    # --------------------------------------------------------------- plumbing
    @property
    def _llm_type(self) -> str:
        return "agentmesh-mock"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name, "persona": self.persona}

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        """Attach ``tools`` to a copy of this model.

        ``_generate`` reads the tool list back out of its keyword arguments, which
        mirrors how LangChain chat models hand tool calling to a model.
        """
        bound = dict(kwargs)
        if tool_choice is not None:
            bound["tool_choice"] = tool_choice
        return self.bind(tools=list(tools), **bound)

    # ------------------------------------------------------------ generation
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        tools = _normalise_tools(kwargs.get("tools"))

        if self._cursor < len(self.script):
            text = self.script[self._cursor]
            self._cursor += 1
            return _result(AIMessage(content=text))

        if tools:
            observations = [message for message in messages if isinstance(message, ToolMessage)]
            if self.call_first_tool and not observations:
                return _result(self._tool_call(tools, messages))
            return _result(AIMessage(content=self._worker_answer(messages, observations)))

        return _result(AIMessage(content=self._planner_answer(messages)))

    # ----------------------------------------------------------------- policy
    def _tool_call(self, tools: Sequence[dict[str, Any]], messages: list[BaseMessage]) -> AIMessage:
        schema = tools[0]
        name = schema["function"]["name"]
        args = _synthesise_args(schema["function"].get("parameters") or {}, self._extract_task(messages))
        return AIMessage(
            content=f"[mock] calling `{name}` to gather evidence.",
            tool_calls=[
                {"name": name, "args": args, "id": f"call_{name}_{self._cursor}", "type": "tool_call"}
            ],
        )

    def _planner_answer(self, messages: list[BaseMessage]) -> str:
        prompt = _as_text(messages[-1].content) if messages else ""
        candidates = _split(_CANDIDATES_RE.search(prompt))
        completed = set(_split(_COMPLETED_RE.search(prompt)))

        for candidate in candidates:
            if candidate not in completed:
                return (
                    f"NEXT: {candidate}\n"
                    f"REASON: `{candidate}` has not contributed to the objective yet, "
                    "and the remaining work is in its remit."
                )
        if candidates:
            return "NEXT: FINISH\nREASON: every candidate agent has already contributed."
        return "NEXT: FINISH\nREASON: no candidate agents are available."

    def _worker_answer(self, messages: list[BaseMessage], observations: list[ToolMessage]) -> str:
        task = self._extract_task(messages) or "the assigned objective"
        lines = [f"[{self.persona}] Findings for: {task[:200]}"]
        for observation in observations[-3:]:
            name = observation.name or "tool"
            lines.append(f"- {name}: {_as_text(observation.content).strip()[:240]}")
        if not observations:
            lines.append("- No tools were required; answered from the model directly.")
        return "\n".join(lines)

    @staticmethod
    def _extract_task(messages: list[BaseMessage]) -> str:
        for message in reversed(messages):
            if isinstance(message, (HumanMessage, SystemMessage)):
                match = _TASK_RE.search(_as_text(message.content))
                if match:
                    return match.group("task").strip()
        return ""


def _result(message: AIMessage) -> ChatResult:
    return ChatResult(generations=[ChatGeneration(message=message)])


def _split(match: re.Match[str] | None) -> list[str]:
    if not match:
        return []
    raw = match.group("value").strip()
    if raw.lower() in _NONE_MARKERS:
        return []
    return [item.strip().strip("`") for item in raw.split(",") if item.strip()]


def _normalise_tools(raw: Any) -> list[dict[str, Any]]:
    """``bind_tools`` hands us OpenAI tool dicts; tolerate BaseTool objects too."""
    if not raw:
        return []
    normalised: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict) and "function" in item:
            normalised.append(item)
        else:
            name = getattr(item, "name", None)
            if not name:
                continue
            schema = getattr(item, "args_schema", None)
            if schema is not None and hasattr(schema, "model_json_schema"):
                parameters = schema.model_json_schema()
            else:
                parameters = {}
            normalised.append(
                {"type": "function", "function": {"name": name, "description": "", "parameters": parameters}}
            )
    return normalised


_TEXT_HINTS = ("query", "expression", "code", "question")
_COUNT_HINTS = ("top_k", "max_results", "limit", "count")


def _synthesise_args(parameters: dict[str, Any], task: str) -> dict[str, Any]:
    """Build plausible arguments from a JSON schema so offline runs are useful."""
    properties: dict[str, Any] = parameters.get("properties") or {}
    required: list[str] = parameters.get("required") or list(properties)
    args: dict[str, Any] = {}
    for name in required:
        spec = properties.get(name) or {}
        if "default" in spec:
            args[name] = spec["default"]
            continue
        lowered = name.lower()
        if lowered in _COUNT_HINTS or spec.get("type") == "integer":
            args[name] = 3
        elif lowered in _TEXT_HINTS or spec.get("type") == "string":
            args[name] = task or "AgentMesh"
        elif spec.get("type") == "boolean":
            args[name] = True
        elif spec.get("type") == "array":
            args[name] = [task] if task else []
    return args


__all__ = ["MockChatModel"]
