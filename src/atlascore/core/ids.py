"""Sortable identifiers (UUIDv7).

The canonical spec requires time-sortable primary keys (UUIDv7 or ULID) so rows
sort in creation order without a separate sequence (docs/schemas §2). We generate
UUIDv7 per RFC 9562 and store it in native Postgres ``uuid`` columns.

UUIDv7 is not in the 3.12 stdlib ``uuid`` module, so we implement it here. The
optional ``ms`` argument lets tests inject a deterministic timestamp.
"""

from __future__ import annotations

import secrets
import time
import uuid

_MAX_MS = (1 << 48) - 1


def uuid7(ms: int | None = None) -> uuid.UUID:
    """Return a UUIDv7 (RFC 9562): 48-bit ms timestamp + version + random bits.

    Layout (128 bits, most-significant first):
        unix_ts_ms : 48 bits   milliseconds since the Unix epoch
        version    :  4 bits   0b0111
        rand_a     : 12 bits   random
        variant    :  2 bits   0b10
        rand_b     : 62 bits   random
    """
    if ms is None:
        ms = time.time_ns() // 1_000_000
    if not 0 <= ms <= _MAX_MS:
        raise ValueError(f"timestamp {ms} out of range for uuid7 (0..{_MAX_MS})")

    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)

    value = (ms & _MAX_MS) << 80
    value |= 0x7 << 76  # version 7
    value |= rand_a << 64
    value |= 0b10 << 62  # RFC 4122 variant
    value |= rand_b
    return uuid.UUID(int=value)


def new_id() -> uuid.UUID:
    """Mint a fresh sortable id. Alias for :func:`uuid7` used as a column default."""
    return uuid7()
