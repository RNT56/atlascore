"""Summary backends (CLAUDE.md §20, docs/mcp §4, docs/schemas §2.5)."""

from __future__ import annotations

from atlascore.enrichment.summaries.base import (
    SummaryBackend,
    SummaryInput,
    SummaryResult,
    get_summary_backend,
)
from atlascore.enrichment.summaries.extractive import ExtractiveSummarizer

__all__ = [
    "ExtractiveSummarizer",
    "SummaryBackend",
    "SummaryInput",
    "SummaryResult",
    "get_summary_backend",
]
