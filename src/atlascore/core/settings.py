"""Typed application settings, sourced from the environment / a gitignored ``.env``.

Mirrors ``.env.example``. Secrets use ``SecretStr`` so they never appear in logs,
reprs, or tracebacks. A ``credential_ref`` on a source names a KEY here; the value
is resolved at runtime and never persisted to a DB column (CLAUDE.md §3, §14).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

EmbeddingBackend = Literal["local", "openai", "google"]
LlmBackend = Literal["local", "api", "none"]
AppEnv = Literal["dev", "staging", "prod", "ci"]


class Settings(BaseSettings):
    """Process configuration. Construct once via :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---
    app_env: AppEnv = "dev"

    # SQLAlchemy/psycopg DSN for the single Postgres 16 instance. The driver MUST
    # be ``postgresql+psycopg`` (psycopg v3). Carries a password, so never logged.
    database_url: str = "postgresql+psycopg://atlas:atlas@localhost:5432/atlascore"

    # Local-disk raw store: immutable, gzipped, content-addressed payloads.
    raw_store_dir: Path = Path("/var/atlascore/rawstore")

    # Optional single-binary search engine; blank => Postgres FTS only (default).
    meili_url: str | None = None

    # --- Embeddings (ONE active model+dim per deployment; switching = backfill) ---
    embedding_backend: EmbeddingBackend = "local"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384
    embedding_api_key: SecretStr | None = None

    # --- LLM summaries ---
    llm_backend: LlmBackend = "none"
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_monthly_spend_cap_eur: float = 10.0

    # --- HTTP fetcher (transparent project UA + contact; never browser-impersonating) ---
    default_user_agent: str = "AtlasCore/0.0 (single-operator research; contact: you@example.com)"

    # --- Alert delivery (one channel; others blank) ---
    alert_webhook_url: SecretStr | None = None
    ntfy_topic: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_pass: SecretStr | None = None
    smtp_from: str | None = None
    smtp_to: str | None = None

    # --- Retention windows (control-plane rotation; duration grammar Nd|Nw|Nm|Ny|Nh) ---
    # audit_log: only retention_class='routine' rows beyond this window are rotated; purge rows are
    # retained forever (R14). crawl_job: only terminal (done|dead) rows beyond this window are pruned.
    audit_routine_retention: str = "180d"
    crawl_job_retention: str = "90d"

    # --- Logging ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- Secrets management (optional SOPS/age path) ---
    sops_age_key_file: Path | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return process-wide settings (cached). Call ``get_settings.cache_clear()`` in tests."""
    return Settings()
