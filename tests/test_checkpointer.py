"""The checkpointer backend must never take the process down with it."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from agentmesh.config import Settings
from agentmesh.graph.checkpointer import open_checkpointer

MEMORY_SAVER_NAMES = {"InMemorySaver", "MemorySaver"}


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "checkpoint_backend": "memory",
        "state_backend": "memory",
        "redis_url": "redis://localhost:6379/0",
    }
    return Settings(**{**base, **overrides})


class _FakeSaver:
    async def asetup(self) -> None:
        return None


class _FakeManager:
    def __init__(self, error: Exception | None) -> None:
        self._error = error
        self.exited = False

    async def __aenter__(self) -> _FakeSaver:
        if self._error is not None:
            raise self._error
        return _FakeSaver()

    async def __aexit__(self, *exc_info: object) -> bool:
        self.exited = True
        return False


def _fake_redis_saver(monkeypatch: pytest.MonkeyPatch, error: Exception | None) -> list[_FakeManager]:
    """Stand in for langgraph's AsyncRedisSaver, installed or not."""
    managers: list[_FakeManager] = []

    class FakeAsyncRedisSaver:
        @staticmethod
        def from_conn_string(url: str) -> _FakeManager:
            manager = _FakeManager(error)
            managers.append(manager)
            return manager

    module = types.ModuleType("langgraph.checkpoint.redis.aio")
    module.AsyncRedisSaver = FakeAsyncRedisSaver
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.redis.aio", module)
    return managers


async def test_the_memory_backend_yields_an_in_memory_saver() -> None:
    async with open_checkpointer(_settings()) as saver:
        assert type(saver).__name__ in MEMORY_SAVER_NAMES


async def test_a_healthy_redis_checkpointer_is_used_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    managers = _fake_redis_saver(monkeypatch, None)

    async with open_checkpointer(_settings(checkpoint_backend="redis")) as saver:
        assert isinstance(saver, _FakeSaver)

    assert managers[0].exited is True


async def test_a_server_without_search_falls_back_to_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    error = RuntimeError("unknown command 'FT.INFO', with args beginning with: 'checkpoints'")
    managers = _fake_redis_saver(monkeypatch, error)

    async with open_checkpointer(_settings(checkpoint_backend="redis")) as saver:
        assert type(saver).__name__ in MEMORY_SAVER_NAMES

    assert managers[0].exited is True


async def test_an_unreachable_redis_falls_back_to_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    managers = _fake_redis_saver(monkeypatch, ConnectionError("connection refused"))

    async with open_checkpointer(_settings(checkpoint_backend="redis")) as saver:
        assert type(saver).__name__ in MEMORY_SAVER_NAMES

    assert managers[0].exited is True


async def test_the_missing_optional_dependency_falls_back_to_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.redis.aio", None)

    async with open_checkpointer(_settings(checkpoint_backend="redis")) as saver:
        assert type(saver).__name__ in MEMORY_SAVER_NAMES
