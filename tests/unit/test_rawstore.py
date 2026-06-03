"""Unit tests for the immutable content-addressed gzip raw store (disk only)."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from atlascore.core.hashing import content_hash
from atlascore.storage.rawstore import RawStore

pytestmark = pytest.mark.unit


def test_put_writes_gzip_at_content_addressed_path(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    data = b"<rss>payload</rss>"
    blob = store.put(data)
    assert blob.created is True
    assert blob.content_hash == content_hash(data)
    assert blob.size_bytes == len(data)  # uncompressed size
    full = tmp_path / blob.relpath
    assert full.exists() and full.suffix == ".gz"
    assert gzip.decompress(full.read_bytes()) == data


def test_put_is_idempotent_on_identical_bytes(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    first = store.put(b"same")
    second = store.put(b"same")
    assert first.created is True
    assert second.created is False  # dedupe: already stored
    assert first.relpath == second.relpath


def test_get_round_trips(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    data = b'{"k": "v"}'
    blob = store.put(data)
    assert store.get(blob.relpath) == data


def test_no_temp_files_left_behind(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    store.put(b"abc")
    leftovers = [p.name for p in tmp_path.rglob("*.tmp")]
    assert leftovers == []


def test_exists(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    digest = content_hash(b"present")
    assert store.exists(digest) is False
    store.put(b"present")
    assert store.exists(digest) is True
