from __future__ import annotations

import pytest

from agentmesh.agents.registry import resolve_agents
from agentmesh.config import Settings
from agentmesh.events.bus import init_bus
from agentmesh.graph.builder import build_context, build_graph
from agentmesh.graph.state import initial_state
from agentmesh.schemas import EventType
from agentmesh.storage.memory import MemoryStore


@pytest.fixture
def store(settings: Settings) -> MemoryStore:
    memory_store = MemoryStore(settings)
    init_bus(memory_store)
    return memory_store


async def test_the_supervisor_dispatches_every_specialist_then_finishes(
    store: MemoryStore, settings: Settings
) -> None:
    graph = build_graph(resolve_agents(["researcher", "writer"]), settings=settings)
    final = await graph.ainvoke(
        initial_state(
            run_id="run_graph",
            task="Write a brief about Redis streams",
            candidates=["researcher", "writer"],
            max_steps=4,
        ),
        {"configurable": {"thread_id": "thread-graph-1"}},
    )

    assert final["completed"] == ["researcher", "writer"]
    assert final["steps"] == 3
    assert "[writer]" in final["final_answer"]
    assert final["errors"] == {}

    events = await store.read_events("run_graph")
    routes = [event.data["next"] for event in events if event.type is EventType.SUPERVISOR_ROUTE]
    assert routes == ["researcher", "writer"]
    assert any(event.type is EventType.TOOL_STARTED for event in events)
    assert any(event.type is EventType.AGENT_FINISHED for event in events)


async def test_each_specialist_contributes_a_report(store: MemoryStore, settings: Settings) -> None:
    graph = build_graph(resolve_agents(["researcher", "writer"]), settings=settings)
    final = await graph.ainvoke(
        initial_state(
            run_id="run_context",
            task="Summarise the current approach",
            candidates=["researcher", "writer"],
            max_steps=4,
        ),
        {"configurable": {"thread_id": "thread-graph-2"}},
    )
    assert "researcher" in final["results"]
    assert "writer" in final["results"]
    assert final["scratchpad"]


async def test_the_step_budget_stops_the_run(store: MemoryStore, settings: Settings) -> None:
    graph = build_graph(resolve_agents(["researcher", "analyst", "writer"]), settings=settings)
    final = await graph.ainvoke(
        initial_state(
            run_id="run_budget",
            task="do everything",
            candidates=["researcher", "analyst", "writer"],
            max_steps=1,
        ),
        {"configurable": {"thread_id": "thread-graph-3"}},
    )

    assert final["completed"] == ["researcher"]
    assert final["steps"] == 2
    assert final["final_answer"]


def test_build_context_is_empty_before_any_report() -> None:
    assert build_context({"results": {}, "errors": {}}) == ""


def test_build_context_includes_failures() -> None:
    context = build_context({"results": {"writer": "done"}, "errors": {"researcher": "boom"}})
    assert "### writer" in context
    assert "- researcher: boom" in context
