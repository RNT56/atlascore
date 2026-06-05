"""Contract tests for the cloud embedding backends (B21; respx, no live network)."""

from __future__ import annotations

import httpx
import pytest
import respx

from atlascore.core.errors import ConfigError
from atlascore.core.settings import Settings
from atlascore.enrichment.embeddings.base import get_embedding_backend
from atlascore.enrichment.embeddings.cloud import GoogleEmbeddingBackend, OpenAIEmbeddingBackend

pytestmark = pytest.mark.unit


def _settings(backend: str, dim: int = 3) -> Settings:
    return Settings(
        embedding_backend=backend,  # type: ignore[arg-type]
        embedding_model="text-embedding-3-small",
        embedding_dim=dim,
        embedding_api_key="ek",  # pragma: allowlist secret
    )


@respx.mock
def test_openai_embeddings_parsed_in_order() -> None:
    respx.post("https://api.openai.com/v1/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.4, 0.5, 0.6]},
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                ]
            },
        )
    )
    vecs = OpenAIEmbeddingBackend(_settings("openai")).embed(["a", "b"])
    assert vecs == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]  # re-sorted by index


@respx.mock
def test_google_embeddings_parsed() -> None:
    respx.post(url__startswith="https://generativelanguage.googleapis.com").mock(
        return_value=httpx.Response(
            200, json={"embeddings": [{"values": [0.1, 0.2, 0.3]}, {"values": [0.4, 0.5, 0.6]}]}
        )
    )
    vecs = GoogleEmbeddingBackend(_settings("google")).embed(["a", "b"])
    assert vecs == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]


@respx.mock
def test_dim_mismatch_raises_r8() -> None:
    respx.post("https://api.openai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})
    )
    with pytest.raises(ConfigError, match="R8"):
        OpenAIEmbeddingBackend(_settings("openai", dim=3)).embed(["a"])


def test_missing_key_raises() -> None:
    with pytest.raises(ConfigError, match="EMBEDDING_API_KEY"):
        OpenAIEmbeddingBackend(Settings(embedding_backend="openai", embedding_api_key=None))


def test_factory_returns_cloud_backends() -> None:
    assert isinstance(get_embedding_backend(_settings("openai")), OpenAIEmbeddingBackend)
    assert isinstance(get_embedding_backend(_settings("google")), GoogleEmbeddingBackend)
