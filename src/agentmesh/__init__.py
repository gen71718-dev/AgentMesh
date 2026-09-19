"""AgentMesh - a multi-agent orchestration service built on LangGraph.

Public surface:

* ``agentmesh.main:create_app``  - the FastAPI application factory
* ``agentmesh.cli:main``         - the ``agentmesh`` command line entry point
* ``agentmesh.agents``           - agent definitions + registry
* ``agentmesh.tools``            - tool definitions + registry
* ``agentmesh.graph``            - the LangGraph supervisor topology
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"

