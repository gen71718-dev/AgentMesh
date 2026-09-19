from __future__ import annotations

from agentmesh.config import Settings


def test_cors_origins_parses_a_comma_separated_list() -> None:
    settings = Settings(cors_origins="https://a.example, https://b.example")
    assert settings.cors_origin_list == ["https://a.example", "https://b.example"]


def test_cors_origins_handles_the_wildcard() -> None:
    assert Settings(cors_origins="*").cors_origin_list == ["*"]
    assert Settings(cors_origins="").cors_origin_list == []


def test_default_agent_list_is_trimmed() -> None:
    settings = Settings(default_agents=" researcher , analyst ,")
    assert settings.default_agent_list == ["researcher", "analyst"]


def test_redis_url_is_masked_in_logs() -> None:
    settings = Settings(redis_url="redis://user:sup3rsecret@cache.internal:6379/2")
    assert settings.masked_redis_url == "redis://user:***@cache.internal:6379/2"
    assert "sup3rsecret" not in settings.masked_redis_url


def test_masked_redis_url_leaves_credentials_free_urls_alone() -> None:
    settings = Settings(redis_url="redis://localhost:6379/0")
    assert settings.masked_redis_url == "redis://localhost:6379/0"


def test_queue_execution_requires_the_redis_backend() -> None:
    problems = Settings(execution_mode="queue", state_backend="memory").validate_runtime()
    assert any("execution_mode=queue" in problem for problem in problems)


def test_python_tool_is_reported_as_risky() -> None:
    problems = Settings(enable_python_tool=True).validate_runtime()
    assert any("enable_python_tool" in problem for problem in problems)


def test_a_default_configuration_has_no_warnings() -> None:
    assert Settings(llm_provider="mock", state_backend="memory", execution_mode="inline").validate_runtime() == []

