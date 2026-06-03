"""Injectable clock. UTC-aware everywhere (CLAUDE.md §18; docs/schemas §2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """A source of the current time. Inject this so time can be frozen in tests."""

    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC ``datetime``."""
        ...


class SystemClock:
    """The real wall clock, always returning timezone-aware UTC time."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock pinned to a fixed instant, for deterministic tests."""

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._instant = instant.astimezone(UTC)

    def now(self) -> datetime:
        return self._instant
