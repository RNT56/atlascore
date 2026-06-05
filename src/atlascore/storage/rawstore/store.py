"""Immutable, content-addressed, gzipped raw store on local disk (CLAUDE.md §12).

The raw payload is stored *before* parsing and is the evidence of record. Layout:
``<root>/<algo>/<shard>/<rest>.gz`` keyed on the content hash, so identical bytes dedupe
and writes are idempotent. Blobs are write-once; deletion happens only via the audited
purge path (R3, later). Raw bytes never go into Postgres — only the `raw_document` index row.
"""

from __future__ import annotations

import gzip
from dataclasses import dataclass
from pathlib import Path

from atlascore.core.hashing import content_hash, hash_path_parts
from atlascore.core.ids import new_id
from atlascore.core.settings import get_settings


@dataclass(frozen=True)
class RawBlob:
    """The result of storing a payload: its content hash, relative path, size, and whether
    it was newly written (``created=False`` means an identical blob already existed)."""

    content_hash: str
    relpath: str
    size_bytes: int
    created: bool


class RawStore:
    """A content-addressed blob store rooted at a local directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def from_settings(cls) -> RawStore:
        return cls(get_settings().raw_store_dir)

    def _relpath(self, digest: str) -> str:
        algo, shard, rest = hash_path_parts(digest)
        return f"{algo}/{shard}/{rest}.gz"

    def put(self, data: bytes, *, algo: str = "sha256") -> RawBlob:
        """Store ``data`` gzipped, content-addressed. Idempotent: identical bytes are a no-op.

        The write is atomic (temp file + ``os.replace``) so a crash never leaves a partial blob.
        ``size_bytes`` is the *uncompressed* payload size.
        """
        digest = content_hash(data, algo=algo)
        relpath = self._relpath(digest)
        full = self.root / relpath
        if full.exists():
            return RawBlob(digest, relpath, len(data), created=False)
        full.parent.mkdir(parents=True, exist_ok=True)
        tmp = full.parent / f".{full.name}.{new_id().hex}.tmp"
        try:
            tmp.write_bytes(gzip.compress(data))
            tmp.replace(full)  # atomic rename within the same filesystem
        finally:
            tmp.unlink(missing_ok=True)
        return RawBlob(digest, relpath, len(data), created=True)

    def get(self, relpath: str) -> bytes:
        """Read and decompress a stored blob by its relative path."""
        return gzip.decompress((self.root / relpath).read_bytes())

    def exists(self, digest: str) -> bool:
        return (self.root / self._relpath(digest)).exists()

    def delete(self, relpath: str) -> bool:
        """Delete a stored blob by relative path. Idempotent (missing file is a no-op).

        **The only place a raw blob is removed** — reachable solely from the audited ``atlas purge``
        path (R3, §12), and only once the last ``document`` referencing the raw is gone. Returns
        True if a file was actually removed.
        """
        full = self.root / relpath
        existed = full.exists()
        full.unlink(missing_ok=True)
        return existed
