"""Unit tests for typed settings + secret hygiene."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from atlascore.core.settings import Settings, get_settings

pytestmark = pytest.mark.unit

# Env vars that ambient shells / CI may set; cleared so default-value tests are hermetic.
_ATLAS_ENV_VARS = (
    "APP_ENV",
    "DATABASE_URL",
    "EMBEDDING_BACKEND",
    "EMBEDDING_DIM",
    "LLM_BACKEND",
    "LLM_API_KEY",
)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ATLAS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    s = Settings(_env_file=None)  # type: ignore[call-arg]  # pydantic-settings init kwarg
    assert s.app_env == "dev"
    assert s.embedding_backend == "local"
    assert s.embedding_dim == 384
    assert s.llm_backend == "none"
    assert s.database_url.startswith("postgresql+psycopg://")


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "ci")
    monkeypatch.setenv("EMBEDDING_DIM", "768")
    monkeypatch.setenv("LLM_API_KEY", "super-secret-value")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.app_env == "ci"
    assert s.embedding_dim == 768
    assert isinstance(s.llm_api_key, SecretStr)


def test_secret_never_leaks_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "super-secret-value")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "super-secret-value" not in repr(s)
    assert s.llm_api_key is not None
    assert "super-secret-value" not in str(s.llm_api_key)
    assert s.llm_api_key.get_secret_value() == "super-secret-value"


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()
