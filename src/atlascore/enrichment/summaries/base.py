"""The summary backend interface + factory (CLAUDE.md §20, docs/mcp §4, docs/schemas §2.5).

A summary backend turns an ordered set of documents into a cited :class:`SummaryResult`. Each
backend exposes a stable ``model_name``/``model_version``/``prompt_version`` (the three identity
fields that, with ``source_hash``, form the R4 cache key).

MVP ships the deterministic, dependency-free :class:`ExtractiveSummarizer` as the ``local`` backend
so the pipeline runs offline / in CI and never re-summarizes an identical tuple. Hosted LLM backends
(``LLM_BACKEND=api``) are pluggable behind the existing ``api`` extra (a hard
``LLM_MONTHLY_SPEND_CAP_EUR`` applies, and document text egresses to a third-party processor —
§16/§20). ``LLM_BACKEND=none`` means **no backend** (cache-only): the factory returns ``None`` and
``get_document_summary`` returns ``null`` + a reason on a cache miss. See ADR 0022.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from atlascore.core.errors import ConfigError
from atlascore.core.settings import Settings, get_settings


@dataclass(frozen=True)
class SummaryInput:
    """One member document handed to the summarizer (body is untrusted, already-sanitized text)."""

    document_id: str
    title: str | None
    body_text: str | None


@dataclass(frozen=True)
class SummaryResult:
    """A produced summary: text + per-document claim citations + a token-cost estimate."""

    summary_text: str
    # internal document id -> the claims (sentences) it supports. Never an external URL.
    citations: dict[str, list[str]] = field(default_factory=dict)
    token_cost: int = 0


@runtime_checkable
class SummaryBackend(Protocol):
    """Summarizes an ordered set of documents into a cited result. Holds no per-call state."""

    @property
    def model_name(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    def summarize(self, docs: list[SummaryInput]) -> SummaryResult:
        """Return a cited summary of ``docs`` (order is significant — part of the cache key)."""
        ...


def get_summary_backend(settings: Settings | None = None) -> SummaryBackend | None:
    """Resolve the configured summary backend, or ``None`` for ``LLM_BACKEND=none`` (cache-only).

    ``local`` → the deterministic extractive summarizer (always available). ``api`` is recognized
    but not wired in MVP — it raises a clear :class:`ConfigError` pointing at the ``api`` extra and
    ADR 0022 rather than silently degrading.
    """
    settings = settings or get_settings()
    if settings.llm_backend == "none":
        return None
    if settings.llm_backend == "local":
        from atlascore.enrichment.summaries.extractive import ExtractiveSummarizer

        return ExtractiveSummarizer()
    raise ConfigError(
        f"LLM backend {settings.llm_backend!r} is not wired in MVP (B12 ships the deterministic "
        "'local' extractive backend; hosted models need the `api` extra — see ADR 0022)"
    )
