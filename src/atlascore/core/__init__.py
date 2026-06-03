"""Core foundation: settings, IDs, clock, logging, and base errors.

No I/O at import time and no global mutable state (CLAUDE.md §18). Clocks and
settings are dependency-injectable so downstream code stays deterministic and
testable.
"""

from __future__ import annotations

from atlascore.core.clock import Clock, SystemClock
from atlascore.core.errors import AtlasError, ConfigError, PolicyError
from atlascore.core.ids import new_id, uuid7
from atlascore.core.settings import Settings, get_settings

__all__ = [
    "AtlasError",
    "Clock",
    "ConfigError",
    "PolicyError",
    "Settings",
    "SystemClock",
    "get_settings",
    "new_id",
    "uuid7",
]
