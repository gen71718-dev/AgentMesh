"""The shared graph state.

Every value here must survive a checkpointer round-trip (msgpack for the Redis
and in-memory savers), so the state deliberately holds plain dicts, lists and
strings - no live objects, no clients, no emitters.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    """State threaded through the supervisor graph."""

    run_id: str
    task: str
    messages: Annotated[list[AnyMessage], add_messages]
    candidates: list[str]
    completed: list[str]
    next_agent: str
    results: dict[str, str]
    errors: dict[str, str]
    scratchpad: list[str]
    trace: list[dict[str, Any]]
    steps: int
    max_steps: int
    final_answer: str


def initial_state(
    *,
    run_id: str,
    task: str,
    candidates: list[str],
    max_steps: int,
) -> AgentState:
    """Build the state a run starts from."""
    return AgentState(
        run_id=run_id,
        task=task,
        messages=[],
        candidates=list(candidates),
        completed=[],
        next_agent="",
        results={},
        errors={},
        scratchpad=[],
        trace=[],
        steps=0,
        max_steps=max_steps,
        final_answer="",
    )


__all__ = ["AgentState", "initial_state"]
