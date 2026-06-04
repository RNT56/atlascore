"""Resolve a source's ``credential_ref`` to its secret value at runtime (CLAUDE.md §3, §14).

``credential_ref`` names an **environment key** (e.g. ``FRED_API_KEY``), never the value. The
value is read from the process environment at fetch time and never persisted to any DB column,
log, or repr. In production the deploy loads secrets into the environment (systemd
``EnvironmentFile`` after ``sops -d``); in dev/tests they are exported / monkeypatched.
"""

from __future__ import annotations

import os


def resolve_credential(ref: str | None) -> str | None:
    """Return the secret named by env key ``ref`` (``None`` if ``ref`` is None or unset).

    The returned value is sensitive: never log it, never write it to a DB column, and never
    place it anywhere ``sanitize_for_storage`` does not cover.
    """
    if ref is None:
        return None
    return os.environ.get(ref)
