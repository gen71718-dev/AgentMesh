"""Test configuration.

The suite runs fully offline against the deterministic mock model and the
in-memory store, so no API key, network access or Redis instance is required.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from httpx import ASGITransport, AsyncClient

# Set unconditionally: environment variables outrank a developer's .env file.
os.environ["AGENTMESH_LLM_PROVIDER"] = "mock"
os.environ["AGENTMESH_STATE_BACKEND"] = "memory"
os.environ["AGENTMESH_CHECKPOINT_BACKEND"] = "memory"
os.environ["AGENTMESH_EXECUTION_MODE"] = "inline"
os.environ["AGENTMESH_LOG_LEVEL"] = "WARNING"
os.environ["AGENTMESH_LOG_JSON"] = "false"

from agentmesh.config import Settings, reload_settings
from agentmesh.main import create_app

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


@pytest.fixture
def settings() -> Settings:
    reload_settings()
    return Settings(
        llm_provider="mock",
        state_backend="memory",
        checkpoint_backend="memory",
        execution_mode="inline",
        log_level="WARNING",
        log_json=False,
        default_agents="researcher,writer",
        max_supervisor_steps=4,
        max_tool_iterations=2,
    )


@pytest.fixture
async def app(settings: Settings):
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as async_client:
        yield async_client


@pytest.fixture
def wait_run(client: AsyncClient):
    """``await wait_run(run_id)`` -> the terminal run record."""

    async def _wait(run_id: str, timeout: float = 15.0) -> dict:  # noqa: ASYNC109
        return await wait_for_run(client, run_id, timeout=timeout)

    return _wait


async def wait_for_run(client: AsyncClient, run_id: str, *, timeout: float = 15.0) -> dict:  # noqa: ASYNC109
    """Poll a run until it reaches a terminal status."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    record: dict = {}
    while loop.time() < deadline:
        response = await client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        record = response.json()
        if record["status"] in TERMINAL_STATUSES:
            return record
        await asyncio.sleep(0.02)
    raise AssertionError(f"run {run_id} did not finish in {timeout}s (last status {record.get('status')!r})")
