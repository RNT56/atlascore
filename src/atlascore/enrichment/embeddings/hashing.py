"""A deterministic, dependency-free embedding backend (the MVP ``local`` backend).

This is a classic *hashing vectorizer*: each token is hashed to a bucket (and a sign) and the
bucket counts are L2-normalized. It needs no model download and no heavy dependency, so the
enrichment pipeline runs offline and in CI, and re-embedding the same text is **bit-for-bit
reproducible** (which the R13 ``source_hash`` skip relies on). It captures lexical overlap only —
not true semantics — so it is a real, useful baseline that a sentence-transformers / cloud backend
can later replace via the R8 backfill+reindex runbook (same ``dim`` → no reindex). See ADR 0021.

Determinism note: Python's builtin ``hash`` is salted per process (``PYTHONHASHSEED``), so it is
NOT used here. Token bucketing uses BLAKE2b (stable across processes and runs).
"""

from __future__ import annotations

import hashlib
import math
import re

_TOKEN_RE = re.compile(r"[0-9a-z]+")

MODEL_NAME = "hashing"
MODEL_VERSION = "1.0.0"


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _bucket_and_sign(token: str, dim: int) -> tuple[int, float]:
    """Map a token to a (bucket, sign) deterministically via BLAKE2b."""
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    bucket = value % dim
    sign = 1.0 if (value >> 63) & 1 else -1.0
    return bucket, sign


class HashingEmbeddingBackend:
    """Deterministic hashing embedder producing L2-normalized ``dim``-vectors."""

    def __init__(self, *, dim: int) -> None:
        if dim < 1:
            raise ValueError(f"embedding dim must be >= 1, got {dim}")
        self._dim = dim

    @property
    def model_name(self) -> str:
        return MODEL_NAME

    @property
    def model_version(self) -> str:
        return MODEL_VERSION

    @property
    def dim(self) -> int:
        return self._dim

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in _tokens(text):
            bucket, sign = _bucket_and_sign(token, self._dim)
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:  # empty / no-token text → zero vector (still a valid fixed-dim row)
            return vec
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]
