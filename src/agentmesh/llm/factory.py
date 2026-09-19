"""Provider-agnostic chat model factory."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from agentmesh.config import Settings, get_settings
from agentmesh.errors import ConfigurationError
from agentmesh.llm.mock import MockChatModel
from agentmesh.logger import get_logger

log = get_logger("llm")

_CACHE: dict[tuple[str, str], BaseChatModel] = {}

_PIP_HINTS = {
    "openai": 'pip install "agentmesh[openai]"',
    "anthropic": 'pip install "agentmesh[anthropic]"',
    "ollama": 'pip install "agentmesh[ollama]"',
}


def build_chat_model(
    role: str = "default",
    *,
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    settings: Settings | None = None,
) -> BaseChatModel:
    """Return a chat model for ``role``.

    ``role`` matters for the offline ``mock`` provider, which uses it as a
    persona so that logs and summaries stay readable.
    """
    settings = settings or get_settings()
    provider = (provider or settings.llm_provider).lower()
    model = model or settings.llm_model
    temperature = settings.llm_temperature if temperature is None else temperature

    key = (f"{role}:{provider}:{model}", f"{temperature}")
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    built = _build(provider, role, model, temperature, settings)
    _CACHE[key] = built
    return built


def _build(provider: str, role: str, model: str, temperature: float, settings: Settings) -> BaseChatModel:
    if provider == "mock":
        return MockChatModel(persona=role, model_name=model)

    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ConfigurationError(_missing("openai")) from exc
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=settings.openai_api_key or None,
            base_url=settings.openai_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ConfigurationError(_missing("anthropic")) from exc
        return ChatAnthropic(  # type: ignore[call-arg]
            model=model,
            temperature=temperature,
            api_key=settings.anthropic_api_key or None,
            base_url=settings.anthropic_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    if provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ConfigurationError(_missing("ollama")) from exc
        return ChatOllama(
            model=model,
            temperature=temperature,
            base_url=settings.ollama_base_url,
        )

    raise ConfigurationError(
        f"Unknown LLM provider {provider!r}. Expected one of: mock, openai, anthropic, ollama."
    )


def _missing(provider: str) -> str:
    return (
        f"LLM provider {provider!r} needs its LangChain integration package. "
        f"Install it with: {_PIP_HINTS[provider]}"
    )


def llm_info(settings: Settings | None = None) -> dict[str, Any]:
    """Describe the configured provider without instantiating it (used by `doctor`)."""
    settings = settings or get_settings()
    info: dict[str, Any] = {
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
    }
    if settings.llm_provider == "openai":
        info["api_key_configured"] = bool(settings.openai_api_key)
    elif settings.llm_provider == "anthropic":
        info["api_key_configured"] = bool(settings.anthropic_api_key)
    elif settings.llm_provider == "ollama":
        info["base_url"] = settings.ollama_base_url
    return info


def reset_cache() -> None:
    _CACHE.clear()


__all__ = ["build_chat_model", "llm_info", "reset_cache"]
