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

EmbeddingBackend = Literal["local", "sentence-transformers", "openai", "google"]
LlmBackend = Literal["local", "api", "none"]
LlmProvider = Literal["openai", "anthropic", "google"]
NerBackend = Literal["rules", "spacy"]
TikaMode = Literal["none", "subprocess", "server"]
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
    # Default matches the compose/CI role (``atlascore``); overridden by .env in any
    # real deployment. Must agree with the compose POSTGRES_* values (see .env.example).
    database_url: str = (
        "postgresql+psycopg://atlascore:change-me-local-only@localhost:5432/atlascore"
    )

    # Local-disk raw store: immutable, gzipped, content-addressed payloads.
    raw_store_dir: Path = Path("/var/atlascore/rawstore")

    # Optional single-binary search engine; blank => Postgres FTS only (default).
    meili_url: str | None = None

    # --- Embeddings (ONE active model+dim per deployment; switching = backfill, R8) ---
    # `local` = the deterministic hashing backend (offline/CI default). `sentence-transformers` =
    # the real local ST embedder (needs the `local-embeddings` extra). `openai`/`google` = cloud
    # (needs the `cloud` extra). EMBEDDING_DIM must match the active model's output dim (R8).
    embedding_backend: EmbeddingBackend = "local"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384
    embedding_api_key: SecretStr | None = None
    embedding_request_timeout: float = 30.0
    embedding_max_batch: int = 64

    # --- LLM summaries ---
    llm_backend: LlmBackend = "none"
    llm_provider: LlmProvider = "openai"  # which hosted API `api` talks to
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_request_timeout: float = 60.0
    llm_max_output_tokens: int = 512
    llm_monthly_spend_cap_eur: float = 10.0
    # Cloud-egress control: request no-training / zero-retention options where the provider supports
    # it (compliance §16/§20 — personal data egresses to a third party when a cloud backend is used).
    cloud_no_train: bool = True

    # --- NER backend (B21; `rules` = deterministic default, `spacy` needs the local-embeddings extra) ---
    ner_backend: NerBackend = "rules"

    # --- SEC full-filing extraction via sandboxed Tika (B22) ---
    # `none` (default) = metadata-only filings (CI/sandbox-safe). `subprocess` = a resource-limited,
    # non-root, network-less `java -jar tika-app.jar --text`. `server` = a localhost tika-server.
    tika_mode: TikaMode = "none"
    tika_jar_path: Path | None = None  # path to tika-app.jar (subprocess mode)
    tika_server_url: str | None = None  # e.g. http://127.0.0.1:9998 (server mode)
    tika_timeout_seconds: float = 30.0
    tika_max_bytes: int = 16 * 1024 * 1024  # cap extracted-text size (16 MiB)
    tika_rlimit_as_bytes: int = 1024 * 1024 * 1024  # subprocess address-space cap (1 GiB)
    tika_rlimit_cpu_seconds: int = 60  # subprocess CPU-time cap

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

    # --- Liveness heartbeat / dead-man's-switch (optional; recommended for unattended ops) ---
    # An external uptime-monitor ping URL (e.g. healthchecks.io). The single-instance scheduler GETs
    # it after each SUCCESSFUL tick, so if the box / Postgres / scheduler dies the pings stop and the
    # monitor PAGES — turning "silence = undetected outage" into an active alert. Carries a per-check
    # token, so SecretStr (never logged). Blank ⇒ disabled.
    heartbeat_url: SecretStr | None = None
    heartbeat_timeout: float = 10.0

    # --- Retention windows (control-plane rotation; duration grammar Nd|Nw|Nm|Ny|Nh) ---
    # audit_log: only retention_class='routine' rows beyond this window are rotated; purge rows are
    # retained forever (R14). crawl_job: only terminal (done|dead) rows beyond this window are pruned.
    audit_routine_retention: str = "180d"
    crawl_job_retention: str = "90d"

    # --- Alert watchlist (B18; git-tracked governed config, default ./watchlist.yaml) ---
    watchlist_path: Path | None = None

    # --- Scheduler + worker loop (B19; all seconds; the scheduler runs housekeeping inline on
    # cadence buckets and enqueues per-source fetch jobs; the worker drains fetch→parse→enrich) ---
    scheduler_tick_seconds: int = 60
    freshness_interval_seconds: int = 900  # refresh source_health every 15m
    alert_interval_seconds: int = 300  # evaluate + deliver alerts every 5m
    partition_interval_seconds: int = 86400  # ensure yearly partitions daily
    audit_rotation_interval_seconds: int = 86400  # rotate audit_log / prune crawl_job daily
    worker_idle_backoff_max_seconds: float = 30.0  # cap the drain loop's idle sleep

    # --- Logging ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- Secrets management (optional SOPS/age path) ---
    sops_age_key_file: Path | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return process-wide settings (cached). Call ``get_settings.cache_clear()`` in tests."""
    return Settings()
