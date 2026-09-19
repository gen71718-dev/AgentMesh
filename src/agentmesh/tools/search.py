"""Web search tool with a pluggable Tavily backend and an offline fallback."""

from __future__ import annotations

import httpx
from langchain_core.tools import tool

from agentmesh.config import get_settings
from agentmesh.logger import get_logger
from agentmesh.tools.registry import ToolSpec, register

log = get_logger("tools.search")

TAVILY_ENDPOINT = "https://api.tavily.com/search"


def _availability() -> tuple[bool, str]:
    if get_settings().tavily_api_key:
        return True, ""
    return False, "set AGENTMESH_TAVILY_API_KEY to enable live web search"


@tool("web_search")
def web_search(query: str, max_results: int = 5) -> str:
    """Search the public web and return ranked titles, URLs and snippets.

    Use this for facts that may have changed recently, for external references
    and for anything you cannot verify from the local knowledge base.
    """
    settings = get_settings()
    query = (query or "").strip()
    if not query:
        return "web_search error: the query must not be empty"

    if not settings.tavily_api_key:
        return (
            "web_search is not configured (missing AGENTMESH_TAVILY_API_KEY). "
            f"No live results were retrieved for {query!r}; rely on the local knowledge base "
            "or state the limitation explicitly."
        )

    try:
        response = httpx.post(
            TAVILY_ENDPOINT,
            json={
                "api_key": settings.tavily_api_key,
                "query": query,
                "max_results": max(1, min(int(max_results), 10)),
                "search_depth": "basic",
                "include_answer": False,
            },
            timeout=settings.search_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        log.warning("web search failed", extra={"error": str(exc)})
        return f"web_search failed: {type(exc).__name__}: {exc}"
    except ValueError as exc:
        return f"web_search returned an unreadable payload: {exc}"

    results = payload.get("results") or []
    if not results:
        return f"web_search found no results for {query!r}"
    lines = [f"{len(results)} result(s) for {query!r}:"]
    for rank, item in enumerate(results, start=1):
        title = item.get("title") or "(untitled)"
        url = item.get("url") or ""
        snippet = (item.get("content") or "").strip().replace("\n", " ")[:400]
        lines.append(f"{rank}. {title}\n   {url}\n   {snippet}")
    return "\n".join(lines)


register(
    ToolSpec(
        name="web_search",
        description=web_search.description,
        factory=lambda: web_search,
        tags=("search", "network"),
        availability=_availability,
    )
)

__all__ = ["web_search"]
