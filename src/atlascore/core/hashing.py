"""Content hashing for raw + parsed content (docs/schemas §2 invariant 4).

A ``content_hash`` is ``"<algo>:<hexdigest>"`` (e.g. ``"sha256:9f1c…"``). It drives
content-addressed raw storage, dedupe, and re-parse change detection.
"""

from __future__ import annotations

import hashlib

DEFAULT_ALGO = "sha256"


def content_hash(data: bytes, *, algo: str = DEFAULT_ALGO) -> str:
    """Return ``"<algo>:<hexdigest>"`` for ``data``."""
    digest = hashlib.new(algo, data).hexdigest()
    return f"{algo}:{digest}"


def hash_path_parts(digest: str) -> tuple[str, str, str]:
    """Split a content hash into ``(algo, shard, rest)`` for a content-addressed layout.

    ``"sha256:9f1c2b…"`` -> ``("sha256", "9f", "1c2b…")`` so the raw store fans out into
    256 first-byte subdirectories instead of one giant flat directory.
    """
    algo, _, hexdigest = digest.partition(":")
    if not hexdigest or len(hexdigest) < 3:
        raise ValueError(f"not a content hash: {digest!r}")
    return algo, hexdigest[:2], hexdigest[2:]
