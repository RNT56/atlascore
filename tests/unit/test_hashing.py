"""Unit tests for content hashing + the content-addressed path split."""

from __future__ import annotations

import pytest

from atlascore.core.hashing import content_hash, hash_path_parts

pytestmark = pytest.mark.unit


def test_content_hash_is_deterministic_and_prefixed() -> None:
    digest = content_hash(b"hello world")
    assert digest.startswith("sha256:")
    assert digest == content_hash(b"hello world")  # deterministic
    assert content_hash(b"hello world") != content_hash(b"hello world!")


def test_content_hash_known_value() -> None:
    # sha256("") is a well-known constant.
    assert content_hash(b"") == (
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_hash_path_parts_shards_by_first_byte() -> None:
    algo, shard, rest = hash_path_parts("sha256:9f1c2bdeadbeef")
    assert algo == "sha256"
    assert shard == "9f"
    assert rest == "1c2bdeadbeef"


def test_hash_path_parts_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="not a content hash"):
        hash_path_parts("nope")
