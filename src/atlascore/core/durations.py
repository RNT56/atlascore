"""Duration grammar for source retention/freshness (docs/connectors §4.3).

Grammar: ``<N>h | <N>d | <N>w | <N>m | <N>y | "permanent"``
  h=hours, d=days, w=weeks, m=months (~30d), y=years (~365d).

- ``retention`` uses ``d/w/m/y`` or ``permanent`` (no hours).
- ``freshness`` uses ``h/d/w/m/y`` and may NOT be ``permanent`` (a freshness threshold must be finite).

Months/years are calendar approximations (30/365 days) — adequate for freshness-lag
comparison and retention pruning, where exact calendar math is not required.
"""

from __future__ import annotations

import re
from datetime import timedelta

PERMANENT = "permanent"

_UNITS: dict[str, timedelta] = {
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
    "m": timedelta(days=30),
    "y": timedelta(days=365),
}
_PATTERN = re.compile(r"^(\d+)([hdwmy])$")


def parse_duration(
    value: str,
    *,
    allow_permanent: bool = False,
    allow_hours: bool = True,
) -> timedelta | None:
    """Parse a duration string. Returns ``None`` for ``"permanent"`` (when allowed).

    Raises ``ValueError`` on any malformed, zero/negative, or disallowed value so that
    a Pydantic field validator surfaces a clear message.
    """
    text = value.strip()
    if text == PERMANENT:
        if not allow_permanent:
            raise ValueError("'permanent' is not allowed here; a finite duration is required")
        return None
    match = _PATTERN.match(text)
    if match is None:
        raise ValueError(f"invalid duration {value!r}; expected '<N>[hdwmy]' or 'permanent'")
    count, unit = int(match.group(1)), match.group(2)
    if count <= 0:
        raise ValueError(f"duration must be positive: {value!r}")
    if unit == "h" and not allow_hours:
        raise ValueError(f"hours are not allowed here: {value!r}")
    return _UNITS[unit] * count
