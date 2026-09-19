"""Keyword search over a local corpus of markdown/text files.

This is the offline-friendly retrieval tool: point ``AGENTMESH_KNOWLEDGE_DIR``
at a folder of notes and the agents can ground their answers in your own data
without any external service.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from langchain_core.tools import tool

from agentmesh.config import get_settings
from agentmesh.tools.registry import ToolSpec, register

MAX_FILES = 500
MAX_FILE_BYTES = 400_000
SNIPPET_CHARS = 400
_WORD_RE = re.compile(r"[\w\-]{2,}", re.UNICODE)
_STOPWORDS = frozenset(
    {"the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "you", "your", "our", "how"}
)
_SUFFIXES = {".md", ".markdown", ".txt", ".rst"}


@dataclass(frozen=True, slots=True)
class Hit:
    path: Path
    line: int
    score: float
    snippet: str


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _WORD_RE.findall(text) if token.lower() not in _STOPWORDS}


def _iter_documents(root: Path) -> list[Path]:
    documents: list[Path] = []
    for path in sorted(root.rglob("*")):
        if len(documents) >= MAX_FILES:
            break
        if path.is_file() and path.suffix.lower() in _SUFFIXES and path.stat().st_size <= MAX_FILE_BYTES:
            documents.append(path)
    return documents


def search(query: str, top_k: int = 3) -> list[Hit]:
    """Return the best matching passages for ``query``."""
    settings = get_settings()
    if not settings.knowledge_dir:
        return []
    root = Path(settings.knowledge_dir).expanduser()
    if not root.is_dir():
        return []

    wanted = _tokens(query)
    if not wanted:
        return []

    hits: list[Hit] = []
    for path in _iter_documents(root):
        best: Hit | None = None
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            overlap = wanted & _tokens(line)
            if not overlap:
                continue
            score = len(overlap) / len(wanted)
            if best is None or score > best.score:
                best = Hit(path=path, line=number, score=score, snippet=line.strip()[:SNIPPET_CHARS])
        if best is not None and best.score > 0:
            hits.append(best)

    hits.sort(key=lambda hit: (-hit.score, str(hit.path), hit.line))
    return hits[: max(1, min(top_k, 10))]


def _availability() -> tuple[bool, str]:
    settings = get_settings()
    if not settings.knowledge_dir:
        return False, "set AGENTMESH_KNOWLEDGE_DIR to a folder of .md/.txt files"
    if not Path(settings.knowledge_dir).expanduser().is_dir():
        return False, f"{settings.knowledge_dir} does not exist"
    return True, ""


@tool("knowledge_search")
def knowledge_search(query: str, top_k: int = 3) -> str:
    """Search the local knowledge base for passages relevant to a query.

    Returns file paths, line numbers and matching snippets. Prefer this tool
    for questions about internal documentation, runbooks or policies.
    """
    available, reason = _availability()
    if not available:
        return f"knowledge base unavailable ({reason}); no local documents were searched"

    hits = search(query, top_k)
    if not hits:
        return f"no local documents matched {query!r}"
    lines = [f"{len(hits)} match(es) for {query!r}:"]
    for rank, hit in enumerate(hits, start=1):
        lines.append(f"{rank}. {hit.path}:{hit.line} (score {hit.score:.2f})\n   {hit.snippet}")
    return "\n".join(lines)


register(
    ToolSpec(
        name="knowledge_search",
        description=knowledge_search.description,
        factory=lambda: knowledge_search,
        tags=("retrieval", "offline"),
        availability=_availability,
    )
)

__all__ = ["Hit", "knowledge_search", "search"]

