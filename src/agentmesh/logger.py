"""Small logging helper: JSON in containers, readable text on a TTY."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_RESERVED: frozenset[str] = frozenset(
    vars(logging.LogRecord(name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None)).keys()
) | {"asctime", "message", "taskName"}

_TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def __init__(self, *, service: str = "agentmesh") -> None:
        super().__init__()
        self._service = service
        self._extra_keys: tuple[str, ...] = ()

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - stdlib contract
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "service": self._service,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload.setdefault(key, value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    level: str = "INFO",
    *,
    json_output: bool = True,
    service: str = "agentmesh",
) -> None:
    """Install a single stdout handler on the root logger (idempotent)."""
    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(JsonFormatter(service=service))
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FORMAT, datefmt="%H:%M:%S"))

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    for noisy in ("uvicorn.access", "httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))
    logging.getLogger("uvicorn.error").setLevel(root.level)


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a namespaced logger: ``get_logger("worker")`` -> ``agentmesh.worker``."""
    if not name:
        return logging.getLogger("agentmesh")
    if name.startswith("agentmesh"):
        return logging.getLogger(name)
    return logging.getLogger(f"agentmesh.{name}")


__all__ = ["JsonFormatter", "configure_logging", "get_logger"]

