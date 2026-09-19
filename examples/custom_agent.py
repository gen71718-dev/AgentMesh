"""Register a custom tool and agent, then run the graph in-process.

No server and no API key: the default configuration uses the offline mock model,
so this runs anywhere.

    python examples/custom_agent.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from langchain_core.tools import tool

from agentmesh.agents.registry import register_agent, resolve_agents
from agentmesh.agents.spec import AgentDefinition
from agentmesh.config import get_settings
from agentmesh.events.bus import init_bus
from agentmesh.graph.builder import build_graph
from agentmesh.graph.state import initial_state
from agentmesh.storage.memory import MemoryStore
from agentmesh.tools.registry import ToolSpec, register


@tool("file_listing")
def file_listing(directory: str = ".") -> str:
    """List the names of the files in a directory."""
    root = Path(directory)
    if not root.is_dir():
        return f"{directory} is not a directory"
    return "\n".join(sorted(entry.name for entry in root.iterdir())) or "(empty directory)"


def register_custom_tool() -> None:
    register(
        ToolSpec(
            name="file_listing",
            description=file_listing.description,
            factory=lambda: file_listing,
            tags=("filesystem",),
        )
    )


def register_custom_agent() -> None:
    register_agent(
        AgentDefinition(
            name="changelog",
            title="Release Notes Writer",
            description="Turns completed work into a user-facing changelog entry.",
            system_prompt=(
                "You are the Release Notes Writer on a multi-agent team.\n\n"
                "Report format\n"
                "-------------\n"
                "Added    - new user-visible capabilities.\n"
                "Changed  - behaviour that existing users will notice.\n"
                "Fixed    - defects that no longer reproduce.\n\n"
                "One line per change. No version numbers, no dates."
            ),
            tools=["file_listing"],
            order=45,
        )
    )


async def main() -> None:
    register_custom_tool()
    register_custom_agent()

    settings = get_settings()
    store = MemoryStore(settings)
    init_bus(store)

    team = ["researcher", "analyst", "changelog", "writer"]
    graph = build_graph(resolve_agents(team), settings=settings)
    final = await graph.ainvoke(
        initial_state(
            run_id="run_example",
            task="Document the new Redis queue recovery behaviour for users",
            candidates=team,
            max_steps=6,
        ),
        {"configurable": {"thread_id": "example-thread"}},
    )

    print("event log")
    print("---------")
    for event in await store.read_events("run_example"):
        print(f"{event.seq:>3}  {event.type.value:<20} {event.agent or '-':<12} {event.message or ''}")

    print("\nanswer")
    print("------")
    print(final["final_answer"])

    print("\nrouting decisions")
    print("-----------------")
    for entry in final["trace"]:
        print(f"  step {entry.get('step')}: {entry.get('next')} - {entry.get('reason')}")


if __name__ == "__main__":
    asyncio.run(main())
