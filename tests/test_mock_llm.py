from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agentmesh.graph.prompts import SUPERVISOR_SYSTEM_PROMPT, build_supervisor_prompt
from agentmesh.llm.mock import MockChatModel
from agentmesh.tools import get_tool


async def test_the_model_requests_a_tool_before_answering() -> None:
    bound = MockChatModel(persona="researcher").bind_tools([get_tool("calculator")])
    response = await bound.ainvoke([HumanMessage(content="TASK: work out 6*7")])

    assert response.tool_calls
    call = response.tool_calls[0]
    assert call["name"] == "calculator"
    assert call["args"]["expression"] == "work out 6*7"


async def test_the_model_answers_once_it_has_an_observation() -> None:
    bound = MockChatModel(persona="researcher").bind_tools([get_tool("calculator")])
    messages = [
        HumanMessage(content="TASK: work out 6*7"),
        AIMessage(content="", tool_calls=[{"name": "calculator", "args": {}, "id": "call_1"}]),
        ToolMessage(content="6*7 = 42", tool_call_id="call_1", name="calculator"),
    ]
    response = await bound.ainvoke(messages)

    assert not response.tool_calls
    assert "work out 6*7" in response.content
    assert "42" in response.content


async def test_the_planner_picks_the_first_candidate_that_has_not_reported() -> None:
    model = MockChatModel(persona="supervisor")
    prompt = build_supervisor_prompt(
        task="Write a brief",
        candidates=["researcher", "writer"],
        completed=["researcher"],
        results={"researcher": "findings"},
        errors={},
        steps=2,
        max_steps=8,
    )
    response = await model.ainvoke(
        [SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT), HumanMessage(content=prompt)]
    )
    assert "NEXT: writer" in response.content


async def test_the_planner_finishes_when_everyone_has_reported() -> None:
    model = MockChatModel(persona="supervisor")
    prompt = build_supervisor_prompt(
        task="Write a brief",
        candidates=["researcher", "writer"],
        completed=["researcher", "writer"],
        results={"researcher": "a", "writer": "b"},
        errors={},
        steps=3,
        max_steps=8,
    )
    response = await model.ainvoke([HumanMessage(content=prompt)])
    assert "NEXT: FINISH" in response.content


async def test_a_script_overrides_the_default_policy() -> None:
    model = MockChatModel(persona="supervisor", script=["NEXT: analyst\nREASON: scripted"])
    response = await model.ainvoke([HumanMessage(content="anything")])
    assert response.content.startswith("NEXT: analyst")

    follow_up = await model.ainvoke([HumanMessage(content="anything")])
    assert follow_up.content.startswith("NEXT: FINISH")


def test_the_model_identifies_itself() -> None:
    model = MockChatModel(persona="researcher")
    assert model._llm_type == "agentmesh-mock"
    assert model._identifying_params["persona"] == "researcher"
