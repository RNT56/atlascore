"""Load the git-tracked alert watchlist (B18). Absent file ⇒ ``Watchlist.empty()`` (no gating)."""

from __future__ import annotations

from pathlib import Path

import yaml

from atlascore.core.errors import ConfigError
from atlascore.core.settings import get_settings
from atlascore.schemas.watchlist import Watchlist

DEFAULT_WATCHLIST_PATH = Path("watchlist.yaml")


def load_watchlist(path: Path | None = None) -> Watchlist:
    """Read + validate the watchlist YAML; return ``Watchlist.empty()`` when the file is absent.

    Resolution: explicit ``path`` arg → ``Settings.watchlist_path`` → ``watchlist.yaml`` (repo root).
    A malformed file raises ``ConfigError`` (fail loud) rather than silently disabling gating.
    """
    resolved = path or get_settings().watchlist_path or DEFAULT_WATCHLIST_PATH
    if not resolved.exists():
        return Watchlist.empty()
    try:
        data = yaml.safe_load(resolved.read_text()) or {}
        return Watchlist.model_validate(data)
    except (yaml.YAMLError, ValueError) as exc:
        raise ConfigError(f"invalid watchlist {resolved}: {exc}") from exc
