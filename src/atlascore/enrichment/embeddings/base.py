"""The embedding backend interface + factory (CLAUDE.md §20, docs/schemas §2.6).

**ONE active embedding model + dim per deployment (R8).** A backend exposes a stable
``model_name``/``model_version``/``dim`` (written onto every ``embedding`` row for provenance and
mismatch detection) and an ``embed`` method that maps texts → fixed-dimension vectors.

MVP ships the deterministic, dependency-free :class:`HashingEmbeddingBackend` as the ``local``
backend so the pipeline runs offline (no model download, CI-safe) and re-embedding is reproducible.
The heavier real backends are pluggable and live behind the existing optional extras
(``local-embeddings`` → sentence-transformers; ``cloud`` → openai/google); wiring them is a
follow-up that, if the dim differs, requires the R8 backfill+reindex runbook. See ADR 0021.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from atlascore.core.errors import ConfigError
from atlascore.core.settings import Settings, get_settings


@runtime_checkable
class EmbeddingBackend(Protocol):
    """A text → fixed-dimension vector embedder. Implementations hold no per-call state."""

    @property
    def model_name(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    @property
    def dim(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed each text into a ``dim``-length vector (order preserved)."""
        ...


def get_embedding_backend(settings: Settings | None = None) -> EmbeddingBackend:
    """Resolve the configured embedding backend.

    ``local`` → the deterministic MVP backend (always available). ``openai``/``google`` are
    recognized but not wired in MVP — they raise a clear :class:`ConfigError` pointing at the
    optional ``cloud`` extra and ADR 0021 rather than silently degrading.
    """
    settings = settings or get_settings()
    # Imported here (not at module top) to keep the protocol module import-light.
    from atlascore.enrichment.embeddings.hashing import HashingEmbeddingBackend

    if settings.embedding_backend == "local":
        return HashingEmbeddingBackend(dim=settings.embedding_dim)
    raise ConfigError(
        f"embedding backend {settings.embedding_backend!r} is not wired in MVP (B11 ships the "
        "deterministic 'local' backend; cloud backends need the `cloud` extra — see ADR 0021)"
    )
