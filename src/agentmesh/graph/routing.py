"""Parse the supervisor's routing decision.

Kept pure and dependency-free so it can be exhaustively unit tested: real
models drift from the requested format, and a routing bug is silent and
expensive.保持纯函数，零依赖。优点：不会报错，缺点：潜在隐患，会烧token（有终止机制兜底）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

FINISH = "FINISH"

_JSON_BLOB_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)
_ROUTE_RE = re.compile(
    r"\b(?:NEXT|ROUTE|AGENT|WORKER|DECISION|CHOICE)\b\s*(?:IS|:|=|-|>)?\s*[^\w\s]?\s*([A-Za-z0-9_\-]+)",
    re.IGNORECASE,
)
_FINISH_RE = re.compile(r"\bNEXT\b\s*[:=\-]?\s*(FINISH|END|DONE|STOP|COMPLETE)\b", re.IGNORECASE)
_REASON_RE = re.compile(r"\bREASON\b\s*[:=\-]?\s*(?P<reason>.+)", re.IGNORECASE)
_JSON_KEYS = ("next", "next_agent", "agent", "route", "decision", "choice", "worker")
_FINISH_WORDS = ("finish", "finished", "done", "stop", "complete", "completed", "no more")


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """``agent`` is ``None`` when the supervisor decided the run is complete."""

    agent: str | None
    reason: str
    raw: str

    @property
    def is_finish(self) -> bool:
        return self.agent is None

"""路由解析器设计成"解析失败就终止"。因为误判成另一个 agent，代价是浪费一步；误判成不终止，代价是无限烧钱。所以失败方向必须偏向"停"。""""
def parse_route(text: str | None, candidates: list[str] | tuple[str, ...]) -> RouteDecision:
    """Extract the next agent from a supervisor response.

    Resolution order, most explicit first:

    1. a JSON object with a recognised key
    2. an explicit ``NEXT:``-style label
    3. an explicit finish token
    4. a bare mention of one of the candidate names
    5. a bare finish word

    Anything else is treated as "finish", which fails safe: an unparseable
    response stops the run instead of looping forever.
    """
    raw = (text or "").strip()
    if not raw:
        return RouteDecision(None, "the supervisor returned an empty response", raw)

    lookup = {candidate.lower(): candidate for candidate in candidates}

    for blob in _json_objects(raw):
        for key in _JSON_KEYS:
            value = blob.get(key)
            if not isinstance(value, str):
                continue
            reason = _as_reason(blob.get("reason") or blob.get("because") or blob.get("why"))
            resolved = _resolve(value, lookup)
            if resolved is not None:
                return RouteDecision(resolved, reason or f"supervisor chose {resolved}", raw)
            if _is_finish(value):
                return RouteDecision(None, reason or "supervisor reported the objective is met", raw)

    for match in _ROUTE_RE.finditer(raw):
        token = match.group(1)
        if token.lower() in lookup:
            chosen = lookup[token.lower()]
            return RouteDecision(chosen, _reason(raw) or f"supervisor chose {chosen}", raw)
        if _is_finish(token):
            return RouteDecision(None, _reason(raw) or "supervisor reported the objective is met", raw)

    if _FINISH_RE.search(raw):
        return RouteDecision(None, _reason(raw) or "supervisor reported the objective is met", raw)

    lowered = raw.lower()
    for candidate in candidates:
        if re.search(rf"\b{re.escape(candidate.lower())}\b", lowered):
            return RouteDecision(candidate, _reason(raw) or f"supervisor mentioned {candidate}", raw)

    if any(word in lowered for word in _FINISH_WORDS):
        return RouteDecision(None, _reason(raw) or "supervisor reported the objective is met", raw)

    return RouteDecision(None, "could not parse a routing decision; stopping the run", raw)


def _resolve(value: str, lookup: dict[str, str]) -> str | None:
    token = value.strip().strip("`\"'.").lower()
    if token in lookup:
        return lookup[token]
    for name in lookup:
        if name and name in token:
            return lookup[name]
    return None


def _is_finish(value: str) -> bool:
    token = value.strip().strip("`\"'.").lower()
    return token in {"finish", "end", "done", "stop", "complete", "completed", "__end__", "none", "null"}


def _json_objects(text: str) -> list[dict[str, object]]:
    objects: list[dict[str, object]] = []
    for match in _JSON_BLOB_RE.finditer(text):
        try:
            parsed = json.loads(match.group(0))
        except ValueError:
            continue
        if isinstance(parsed, dict):
            objects.append(parsed)
    return objects


def _reason(text: str) -> str:
    match = _REASON_RE.search(text)
    if not match:
        return ""
    return match.group("reason").strip().strip("`\"'")[:300]


def _as_reason(value: object) -> str:
    return value.strip()[:300] if isinstance(value, str) else ""


__all__ = ["FINISH", "RouteDecision", "parse_route"]
