"""Per-host rate limiting (CLAUDE.md §17, docs/connectors §5.1).

MVP = a single fetch worker with in-process per-host limiters, so this is intentionally NOT
thread-safe (the single-fetch-worker startup guard keeps it correct; scaling to >1 fetch worker
requires the documented Postgres `host_rate_state` path). The limiter evenly spaces requests at
``window_seconds / max_requests`` apart — a conservative reading of the documented limit (never
exceeds it). The clock is injected so tests are deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class HostRateLimiter:
    """Tracks the next allowed request time per host and returns how long to wait."""

    _next_allowed: dict[str, datetime] = field(default_factory=dict)

    def acquire(
        self, host: str, *, now: datetime, max_requests: int, window_seconds: float
    ) -> float:
        """Reserve a slot for ``host``; return seconds to wait before issuing the request (0 = now).

        The caller sleeps for the returned duration. Slots are reserved monotonically, so
        concurrent-ish callers in the same process are still spaced apart.
        """
        if max_requests <= 0 or window_seconds <= 0:
            raise ValueError("max_requests and window_seconds must be positive")
        interval = timedelta(seconds=window_seconds / max_requests)
        earliest = self._next_allowed.get(host)
        if earliest is None or now >= earliest:
            self._next_allowed[host] = now + interval
            return 0.0
        wait = (earliest - now).total_seconds()
        self._next_allowed[host] = earliest + interval
        return wait
