"""Exception hierarchy shared across AgentMesh."""

from __future__ import annotations


class AgentMeshError(Exception):
    """Base class for every error raised by AgentMesh."""


class ConfigurationError(AgentMeshError):
    """The runtime configuration is inconsistent or incomplete."""


class RunNotFound(AgentMeshError):
    """No run exists for the requested id."""


class RunCancelled(AgentMeshError):
    """The run was cancelled while executing."""


class AgentNotFound(AgentMeshError):
    """The requested agent is not registered."""


class ToolNotFound(AgentMeshError):
    """The requested tool is not registered."""


class StoreError(AgentMeshError):
    """The persistence/queue backend failed."""


__all__ = [
    "AgentMeshError",
    "AgentNotFound",
    "ConfigurationError",
    "RunCancelled",
    "RunNotFound",
    "StoreError",
    "ToolNotFound",
]

