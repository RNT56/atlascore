"""``source_health`` vocabulary: the per-source health status (docs/schemas §2.12)."""

from __future__ import annotations

from enum import StrEnum


class HealthStatus(StrEnum):
    """Per-source freshness/health status (docs/runbooks §8).

    Precedence when classifying (highest first): ``cooling_down`` (circuit-broken after N
    consecutive failures) > ``stale`` (lag past ``error_after``) > ``warn`` (lag past
    ``warn_after``) > ``error`` (failing but never succeeded) > ``ok``.
    """

    OK = "ok"
    WARN = "warn"
    STALE = "stale"
    ERROR = "error"
    COOLING_DOWN = "cooling_down"

    @property
    def is_healthy(self) -> bool:
        """``ok`` is healthy; everything else surfaces in the digest."""
        return self is HealthStatus.OK
