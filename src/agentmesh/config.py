"""Typed, environment-driven configuration.

All settings are read from ``AGENTMESH_*`` environment variables and/or a local
``.env`` file. List-shaped values are deliberately modelled as comma separated
strings so that no JSON escaping is required in ``.env`` or ``docker-compose``.
"""

from __future__ import annotations

import os
import socket
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "dev", "staging", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LlmProvider = Literal["mock", "openai", "anthropic", "ollama"]
ExecutionMode = Literal["inline", "queue"]
Backend = Literal["memory", "redis"]


class Settings(BaseSettings):
    """Runtime configuration for every AgentMesh process."""

    model_config = SettingsConfigDict(
        env_prefix="AGENTMESH_",
        env_file=(".env",),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ app
    app_name: str = "AgentMesh"
    environment: Environment = "local"
    debug: bool = False
    log_level: LogLevel = "INFO"
    log_json: bool = True
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    api_prefix: str = "/api/v1"
    cors_origins: str = "*"

    # ------------------------------------------------------------------ llm
    llm_provider: LlmProvider = "mock"
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    # ---------------------------------------------------------------- redis
    redis_url: str = "redis://localhost:6379/0"
    redis_namespace: str = "agentmesh"
    redis_ttl_seconds: int = Field(default=86_400, ge=60)
    redis_stream_maxlen: int = Field(default=5_000, ge=1)
    redis_socket_timeout: float = Field(default=5.0, gt=0)

    # -------------------------------------------------------------- storage
    state_backend: Backend = "memory"
    checkpoint_backend: Backend = "memory"

    # ------------------------------------------------------------ execution
    execution_mode: ExecutionMode = "inline"
    worker_concurrency: int = Field(default=4, ge=1, le=64)
    run_timeout_seconds: float = Field(default=300.0, gt=0)
    queue_block_seconds: float = Field(default=5.0, gt=0)
    queue_reclaim_after_seconds: float = Field(default=120.0, gt=0)
    run_history_limit: int = Field(default=100, ge=1, le=1000)

    # -------------------------------------------------------- orchestration
    max_supervisor_steps: int = Field(default=8, ge=1, le=50)
    max_tool_iterations: int = Field(default=3, ge=1, le=20)
    enable_python_tool: bool = False
    default_agents: str = "researcher,analyst,writer"
    knowledge_dir: str | None = None

    # ------------------------------------------------------------- tooling
    tavily_api_key: str | None = None
    search_timeout_seconds: float = Field(default=20.0, gt=0)

    # ------------------------------------------------------------- derived
    @property
    def cors_origin_list(self) -> list[str]:
        """``AGENTMESH_CORS_ORIGINS`` split into a list (comma separated)."""
        raw = (self.cors_origins or "").strip()
        if not raw:
            return []
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def default_agent_list(self) -> list[str]:
        return [name.strip() for name in self.default_agents.split(",") if name.strip()]

    @property
    def masked_redis_url(self) -> str:
        """``redis://user:***@host:6379/0`` - safe to log."""
        parts = urlsplit(self.redis_url)
        if not parts.password:
            return self.redis_url
        userinfo = f"{parts.username or ''}:***"
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, f"{userinfo}@{host}", parts.path, parts.query, parts.fragment))

    @property
    def worker_name(self) -> str:
        """Identifies this process in run records and Redis consumer groups."""
        return f"{socket.gethostname()}-{os.getpid()}"

    def validate_runtime(self) -> list[str]:
        """Return a list of human readable configuration warnings."""
        problems: list[str] = []
        if self.execution_mode == "queue" and self.state_backend != "redis":
            problems.append(
                "execution_mode=queue requires state_backend=redis: the queue must be "
                "visible to separate worker processes (set AGENTMESH_STATE_BACKEND=redis)."
            )
        if self.checkpoint_backend == "redis" and self.state_backend == "memory":
            problems.append(
                "checkpoint_backend=redis with state_backend=memory works but is unusual; "
                "consider AGENTMESH_STATE_BACKEND=redis as well."
            )
        if self.llm_provider == "openai" and not self.openai_api_key:
            problems.append("llm_provider=openai requires AGENTMESH_OPENAI_API_KEY.")
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            problems.append("llm_provider=anthropic requires AGENTMESH_ANTHROPIC_API_KEY.")
        if self.enable_python_tool:
            problems.append(
                "enable_python_tool=true executes model-authored Python inside the pod; "
                "only enable it behind a sandboxed runtime in trusted environments."
            )
        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings instance."""
    return Settings()


def reload_settings() -> Settings:
    """Drop the cache and re-read the environment (used by the CLI/tests)."""
    get_settings.cache_clear()
    return get_settings()


__all__ = [
    "Backend",
    "Environment",
    "ExecutionMode",
    "LlmProvider",
    "LogLevel",
    "Settings",
    "get_settings",
    "reload_settings",
]
