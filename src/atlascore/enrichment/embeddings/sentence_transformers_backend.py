"""Real local embedder via sentence-transformers (B21; behind the ``local-embeddings`` extra).

Import-guarded: ``sentence_transformers`` is imported inside ``__init__`` so this module imports
cleanly with the extra absent (CI keeps the deterministic hashing default). The loaded model's output
dimension is validated against ``EMBEDDING_DIM`` (R8 — one active dim per deployment).
"""

from __future__ import annotations

from atlascore.core.errors import ConfigError
from atlascore.core.settings import Settings, get_settings


class SentenceTransformersEmbeddingBackend:
    """A `sentence-transformers` text→vector embedder (e.g. all-MiniLM-L6-v2, dim 384)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - exercised only with the extra installed
            raise ConfigError(
                "EMBEDDING_BACKEND=sentence-transformers needs the `local-embeddings` extra "
                "(uv sync --extra local-embeddings)"
            ) from exc
        self._model = SentenceTransformer(self._settings.embedding_model)
        actual = int(self._model.get_sentence_embedding_dimension())
        if actual != self._settings.embedding_dim:
            raise ConfigError(
                f"model dim {actual} != EMBEDDING_DIM {self._settings.embedding_dim} (R8 — "
                "set EMBEDDING_DIM to match, then backfill+reindex)"
            )

    @property
    def model_name(self) -> str:
        return "sentence-transformers"

    @property
    def model_version(self) -> str:
        return self._settings.embedding_model

    @property
    def dim(self) -> int:
        return self._settings.embedding_dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            texts, normalize_embeddings=True, batch_size=self._settings.embedding_max_batch
        )
        return [list(map(float, v)) for v in vectors]
