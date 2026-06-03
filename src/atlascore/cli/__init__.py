"""The ``atlas`` command-line control plane (Typer).

The CLI is the only ingestion-management surface and is never exposed to agents
(CLAUDE.md §6). B1 ships the entry point + ``version``; commands land in their
blocks (``source`` in B2, ``crawl``/``parse`` in B3-B5, ``health``/``digest`` in B8, ...).
"""

from __future__ import annotations

from atlascore.cli.main import app

__all__ = ["app"]
