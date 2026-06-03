"""Unit tests for sortable UUIDv7 ids."""

from __future__ import annotations

import uuid

import pytest

from atlascore.core.ids import new_id, uuid7

pytestmark = pytest.mark.unit


def test_uuid7_version_and_variant() -> None:
    u = uuid7()
    assert u.version == 7
    assert u.variant == uuid.RFC_4122


def test_uuid7_is_unique() -> None:
    ids = {uuid7() for _ in range(2000)}
    assert len(ids) == 2000


def test_uuid7_is_time_sortable() -> None:
    earlier = uuid7(ms=1_000)
    later = uuid7(ms=2_000)
    # UUIDs compare by their 128-bit int; the ms timestamp occupies the high bits.
    assert earlier < later


def test_uuid7_rejects_out_of_range_timestamp() -> None:
    with pytest.raises(ValueError, match="out of range"):
        uuid7(ms=-1)
    with pytest.raises(ValueError, match="out of range"):
        uuid7(ms=1 << 48)


def test_new_id_is_uuid7() -> None:
    assert new_id().version == 7
