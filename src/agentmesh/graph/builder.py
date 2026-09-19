"""Assemble the supervisor topology.

    START -> supervisor -> {specialist, specialist, ...} -> supervisor -> END

The supervisor decides *who* acts next; the specialists do the work and always
hand control back. That is the whole orchestration contract, which keeps the
graph easy to reason about and easy to extend with new specialists.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from agentmesh.agents.runtime import AgentRuntime, message_text
from agentmesh.agents.spec import AgentDefinition
from agentmesh.config import Settings, get_settings
from agentmesh.errors import RunCancelled
from agentmesh.events.bus import try_get_bus
from agentmesh.graph.prompts import (
    SUPERVISOR_SYSTEM_PROMPT,
    build_supervisor_prompt,
    clip,
    compose_final_answer,
)
from agentmesh.graph.routing import parse_route
from agentmesh.graph.state import AgentState
from agentmesh.llm.factory import build_chat_model
from agentmesh.logger import get_logger
from agentmesh.schemas import EventType

log = get_logger("graph.builder")

CONTEXT_PER_AGENT_CHARS = 1_500
CONTEXT_TOTAL_CHARS = 6_000


def build_graph(
    agents: Sequence[AgentDefinition],
    *,
    settings: Settings | None = None,
    checkpointer: Any | None = None,
    supervisor_model: BaseChatModel | None = None,
    name: str = "agentmesh",
) -> Any:
    """Compile the supervisor graph for a fixed team of specialists."""
    settings = settings or get_settings()
    definitions = list(agents)

    async def supervisor_node(state: AgentState) -> dict[str, Any]:
        run_id = state.get("run_id", "")
        bus = try_get_bus()
        task = state.get("task", "")
        steps = int(state.get("steps", 0)) + 1
        completed = list(state.get("completed") or [])
        results = dict(state.get("results") or {})
        errors = dict(state.get("errors") or {})
        max_steps = int(state.get("max_steps") or settings.max_supervisor_steps)
        remaining = [name for name in state.get("candidates") or [] if name not in completed]

        if bus is not None and run_id and await bus.store.is_cancelled(run_id):
            raise RunCancelled(f"run {run_id} was cancelled")

        if not remaining or steps > max_steps:
            reason = (
                "no specialist remains" if not remaining else f"the {max_steps} step budget was exhausted"
            )
            return _finalize(state, steps, reason, completed, results, errors)

        prompt = build_supervisor_prompt(
            task=task,
            candidates=remaining,
            completed=completed,
            results=results,
            errors=errors,
            steps=steps,
            max_steps=max_steps,
        )
        model = supervisor_model or build_chat_model("supervisor", temperature=0.0, settings=settings)
        response = await asyncio.wait_for(
            model.ainvoke([SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT), HumanMessage(content=prompt)]),
            timeout=settings.llm_timeout_seconds,
        )
        decision = parse_route(message_text(response), remaining)
        trace = [
            *(state.get("trace") or []),
            {"step": steps, "next": decision.agent or "FINISH", "reason": decision.reason},
        ]

        if decision.agent is None:
            return _finalize(state, steps, decision.reason, completed, results, errors, trace=trace)

        if bus is not None and run_id:
            await bus.emit(
                run_id,
                EventType.SUPERVISOR_ROUTE,
                agent="supervisor",
                message=f"supervisor -> {decision.agent}",
                data={"next": decision.agent, "reason": decision.reason, "step": steps},
            )
        return {"next_agent": decision.agent, "steps": steps, "trace": trace}

    def make_worker(definition: AgentDefinition) -> Any:
        async def worker_node(state: AgentState) -> dict[str, Any]:
            run_id = state.get("run_id", "")
            runtime = AgentRuntime(definition, bus=try_get_bus(), settings=settings)
            result = await runtime.arun(
                state.get("task", ""),
                run_id=run_id,
                context=build_context(state),
            )

            results = dict(state.get("results") or {})
            errors = dict(state.get("errors") or {})
            if result.ok and result.text.strip():
                results[definition.name] = result.text
            else:
                errors[definition.name] = result.error or "the agent produced no output"

            trace = [
                *(state.get("trace") or []),
                {
                    "step": int(state.get("steps", 0)),
                    "agent": definition.name,
                    "ok": result.ok,
                    "iterations": result.iterations,
                    "tools": [record.name for record in result.tool_calls],
                },
            ]
            return {
                "results": results,
                "errors": errors,
                "completed": [*(state.get("completed") or []), definition.name],
                "scratchpad": [*(state.get("scratchpad") or []), f"[{definition.name}] {result.text[:200]}"],
                "trace": trace,
                "messages": [AIMessage(content=f"[{definition.name}] {result.text}")],
            }

        return worker_node

    def route(state: AgentState) -> str:
        return state.get("next_agent") or END

    builder: StateGraph = StateGraph(AgentState)
    builder.add_node("supervisor", supervisor_node)
    for definition in definitions:
        builder.add_node(definition.name, make_worker(definition))

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route,
        {**{definition.name: definition.name for definition in definitions}, END: END},
    )
    for definition in definitions:
        builder.add_edge(definition.name, "supervisor")

    compiled = builder.compile(checkpointer=checkpointer, name=name)
    log.info("graph compiled", extra={"agents": [definition.name for definition in definitions]})
    return compiled


def _finalize(
    state: AgentState,
    steps: int,
    reason: str,
    completed: list[str],
    results: dict[str, str],
    errors: dict[str, str],
    *,
    trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    answer = compose_final_answer(state.get("task", ""), results, errors)
    entry = {"step": steps, "next": "FINISH", "reason": reason}
    base_trace = list(trace) if trace is not None else list(state.get("trace") or [])
    return {
        "next_agent": END,
        "steps": steps,
        "final_answer": answer,
        "trace": [*base_trace, entry],
        "messages": [AIMessage(content=answer)],
    }


def build_context(state: AgentState, *, limit: int = CONTEXT_TOTAL_CHARS) -> str:
    """Everything the team has established so far, as a compact markdown block."""
    results = state.get("results") or {}
    errors = state.get("errors") or {}
    blocks: list[str] = []
    for name, text in results.items():
        blocks.append(f"### {name}\n{clip(text, CONTEXT_PER_AGENT_CHARS)}")
    if errors:
        failures = "\n".join(f"- {name}: {error}" for name, error in errors.items())
        blocks.append(f"### failed specialists\n{failures}")
    if not blocks:
        return ""
    return clip("\n\n".join(blocks), limit)


__all__ = ["build_context", "build_graph"]
