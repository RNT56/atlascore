"""Cloud embedding backends (B21): OpenAI / Google over httpx (respx contract-tested).

Built on raw httpx (not the vendor SDKs) so respx can intercept with no live network. Each backend
resolves its key from ``EMBEDDING_API_KEY``, asserts every returned vector length == ``EMBEDDING_DIM``
(R8 — one active dim per deployment), and never logs the key/URL unsanitized. Cloud embeddings egress
document text to a third party (§16/§20) — prefer a local backend for PII-bearing docs.
"""

from __future__ import annotations

from typing import Any

import httpx

from atlascore.core.errors import ConfigError
from atlascore.core.settings import Settings, get_settings

_OPENAI_BASE = "https://api.openai.com"
_GOOGLE_BASE = "https://generativelanguage.googleapis.com"


class _CloudBase:
    def __init__(
        self, settings: Settings | None = None, *, client: httpx.Client | None = None
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        if self._settings.embedding_api_key is None:
            raise ConfigError(
                f"embedding backend {self._settings.embedding_backend!r} requires EMBEDDING_API_KEY"
            )

    @property
    def model_name(self) -> str:
        return self._settings.embedding_backend

    @property
    def model_version(self) -> str:
        return self._settings.embedding_model

    @property
    def dim(self) -> int:
        return self._settings.embedding_dim

    def _key(self) -> str:
        return self._settings.embedding_api_key.get_secret_value()  # type: ignore[union-attr]

    def _post(
        self, url: str, *, headers: dict[str, str], json_body: dict[str, Any]
    ) -> dict[str, Any]:
        timeout = self._settings.embedding_request_timeout
        if self._client is not None:
            resp = self._client.post(url, headers=headers, json=json_body, timeout=timeout)
        else:
            with httpx.Client() as client:
                resp = client.post(url, headers=headers, json=json_body, timeout=timeout)
        if resp.status_code >= 400:
            raise ConfigError(f"cloud embedding call failed: HTTP {resp.status_code}")
        data: Any = resp.json()
        if not isinstance(data, dict):
            raise ConfigError("cloud embedding returned a non-object response")
        return data

    def _validate(self, vectors: list[list[float]]) -> list[list[float]]:
        for v in vectors:
            if len(v) != self.dim:
                raise ConfigError(
                    f"cloud embedding dim {len(v)} != EMBEDDING_DIM {self.dim} (R8 — backfill+reindex)"
                )
        return vectors


class OpenAIEmbeddingBackend(_CloudBase):
    """OpenAI ``/v1/embeddings`` (e.g. text-embedding-3-small/large)."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        data = self._post(
            f"{_OPENAI_BASE}/v1/embeddings",
            headers={"Authorization": f"Bearer {self._key()}"},
            json_body={"model": self.model_version, "input": texts},
        )
        rows = sorted(data.get("data", []), key=lambda r: r.get("index", 0))
        return self._validate([list(map(float, r["embedding"])) for r in rows])


class GoogleEmbeddingBackend(_CloudBase):
    """Google ``:batchEmbedContents`` (e.g. text-embedding-004)."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self.model_version
        data = self._post(
            f"{_GOOGLE_BASE}/v1beta/models/{model}:batchEmbedContents?key={self._key()}",
            headers={},
            json_body={
                "requests": [
                    {"model": f"models/{model}", "content": {"parts": [{"text": t}]}} for t in texts
                ]
            },
        )
        embeddings = data.get("embeddings", [])
        return self._validate([list(map(float, e["values"])) for e in embeddings])
