from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import os

DEFAULT_OLLAMA_ENDPOINT = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "gemma3:latest"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 20
DEFAULT_OLLAMA_RETRY_COUNT = 1


@dataclass(frozen=True)
class OllamaPlanningConfig:
    enabled: bool
    endpoint: str
    model: str
    timeout_seconds: int
    fallback_model: Optional[str] = None
    retry_count: int = DEFAULT_OLLAMA_RETRY_COUNT


@dataclass(frozen=True)
class OllamaConversationConfig:
    enabled: bool
    endpoint: str
    model: str
    timeout_seconds: int
    fallback_model: Optional[str] = None
    retry_count: int = DEFAULT_OLLAMA_RETRY_COUNT


def load_ollama_planning_config() -> OllamaPlanningConfig:
    return OllamaPlanningConfig(
        enabled=_bool_env("OLLAMA_PLANNING_ENABLED", default=False),
        endpoint=_string_env("OLLAMA_ENDPOINT", DEFAULT_OLLAMA_ENDPOINT),
        model=_string_env("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        timeout_seconds=_positive_int_env("OLLAMA_TIMEOUT_SECONDS", DEFAULT_OLLAMA_TIMEOUT_SECONDS),
        fallback_model=_optional_string_env("OLLAMA_FALLBACK_MODEL"),
        retry_count=_non_negative_int_env("OLLAMA_RETRY_COUNT", DEFAULT_OLLAMA_RETRY_COUNT),
    )


def load_ollama_conversation_config() -> OllamaConversationConfig:
    planning_enabled = _bool_env("OLLAMA_PLANNING_ENABLED", default=False)
    endpoint = _string_env("OLLAMA_ENDPOINT", DEFAULT_OLLAMA_ENDPOINT)
    model = _string_env("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    timeout_seconds = _positive_int_env("OLLAMA_TIMEOUT_SECONDS", DEFAULT_OLLAMA_TIMEOUT_SECONDS)
    fallback_model = _optional_string_env("OLLAMA_FALLBACK_MODEL")
    retry_count = _non_negative_int_env("OLLAMA_RETRY_COUNT", DEFAULT_OLLAMA_RETRY_COUNT)

    return OllamaConversationConfig(
        enabled=_bool_env("OLLAMA_DISCORD_ENABLED", default=planning_enabled),
        endpoint=_string_env("OLLAMA_DISCORD_ENDPOINT", endpoint),
        model=_string_env("OLLAMA_DISCORD_MODEL", model),
        timeout_seconds=_positive_int_env("OLLAMA_DISCORD_TIMEOUT_SECONDS", timeout_seconds),
        fallback_model=_optional_string_env("OLLAMA_DISCORD_FALLBACK_MODEL", fallback_model),
        retry_count=_non_negative_int_env("OLLAMA_DISCORD_RETRY_COUNT", retry_count),
    )


def _string_env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _optional_string_env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    return value


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "y", "on"}


def _positive_int_env(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer value, got: {value!r}") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than 0, got: {value!r}")
    return parsed


def _non_negative_int_env(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer value, got: {value!r}") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be 0 or greater, got: {value!r}")
    return parsed
