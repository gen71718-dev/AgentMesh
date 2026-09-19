"""Catalog endpoints: which agents and tools exist."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from agentmesh.agents.registry import all_agents, get_agent
from agentmesh.errors import AgentNotFound
from agentmesh.schemas import AgentInfo, ToolInfo
from agentmesh.tools.registry import list_tools

router = APIRouter(tags=["catalog"])


@router.get("/agents", response_model=list[AgentInfo], summary="List the registered agents")
async def list_agents() -> list[AgentInfo]:
    return [definition.to_info() for definition in all_agents()]


@router.get("/agents/{name}", response_model=AgentInfo, summary="Describe one agent")
async def describe_agent(name: str) -> AgentInfo:
    try:
        return get_agent(name).to_info()
    except AgentNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/tools", response_model=list[ToolInfo], summary="List the registered tools")
async def list_tools_route() -> list[ToolInfo]:
    return list_tools()


__all__ = ["router"]

