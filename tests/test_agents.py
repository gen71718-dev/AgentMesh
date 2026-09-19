from __future__ import annotations

from typing import Any

import pytest

from agentmesh.agents.registry import agent_names, get_agent, resolve_agents
from agentmesh.agents.runtime import AgentRuntime, build_user_message
from agentmesh.config import Settings
from agentmesh.errors import AgentNotFound
from agentmesh.llm.mock import MockChatModel


class BoomModel(MockChatModel):
    """A model that always fails, so the failure path stays covered."""

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
        raise RuntimeError("model exploded")


def test_builtin_team_is_registered() -> None:
    assert {"researcher", "analyst", "coder", "writer", "critic"} <= set(agent_names())


def test_agent_lookup() -> None:
    assert get_agent("writer").title == "Technical Writer"
    with pytest.raises(AgentNotFound):
        get_agent("ghost")


def test_resolve_agents_preserves_order_and_deduplicates() -> None:
    resolved = resolve_agents(["writer", "researcher", "writer"])
    assert [definition.name for definition in resolved] == ["writer", "researcher"]


def test_resolve_agents_uses_the_default_when_nothing_is_requested() -> None:
    assert [definition.name for definition in resolve_agents(None, default=["critic"])] == ["critic"]
    assert resolve_agents([]) == []


def test_resolve_agents_rejects_an_unknown_name() -> None:
    with pytest.raises(AgentNotFound):
        resolve_agents(["researcher", "ghost"])


async def test_runtime_runs_the_tool_loop(settings: Settings) -> None:
    runtime = AgentRuntime(get_agent("researcher"), bus=None, settings=settings)
    result = await runtime.arun("Investigate Redis streams", run_id="run_runtime")

    assert result.ok is True
    assert result.iterations == 2
    assert [record.name for record in result.tool_calls] == ["web_search"]
    assert "Investigate Redis streams" in result.text


async def test_runtime_reports_a_failing_model(settings: Settings) -> None:
    runtime = AgentRuntime(
        get_agent("writer"),
        model=BoomModel(persona="writer"),
        bus=None,
        settings=settings,
    )
    result = await runtime.arun("anything", run_id="run_runtime")

    assert result.ok is False
    assert "model exploded" in (result.error or "")


def test_the_user_message_carries_the_task_marker() -> None:
    message = build_user_message("do the thing", "### researcher\nfindings")
    assert message.startswith("TASK: do the thing")
    assert "findings" in message
    assert "nothing yet" in build_user_message("do the thing")
