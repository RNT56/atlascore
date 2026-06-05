"""Sandboxed parsing of risky fetched files (B22). Currently: Apache Tika for SEC filings."""

from __future__ import annotations

from atlascore.security.sandbox.tika import TikaClient, tika_available

__all__ = ["TikaClient", "tika_available"]
