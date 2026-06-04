"""Deterministic keyword-based topic tagging (CLAUDE.md §8, docs/schemas §2.4 ``topics``).

A small, transparent keyword→topic map — no model, reproducible, CI-safe. It tags the coarse
subject buckets the MVP sources cover (macro / finance-filings / cybersecurity / technology), which
power the ``search_documents`` ``topics`` filter and dashboards. A learned topic model is a later
upgrade; this never fabricates a topic that has no keyword hit.
"""

from __future__ import annotations

import re

# topic -> the keywords that imply it (matched as whole words, case-insensitive).
_TOPIC_KEYWORDS: dict[str, frozenset[str]] = {
    "macroeconomics": frozenset(
        {
            "inflation",
            "cpi",
            "gdp",
            "unemployment",
            "payrolls",
            "fomc",
            "monetary",
            "rate",
            "rates",
            "federal reserve",
            "central bank",
            "recession",
            "deflation",
            "yield",
            "treasury",
        }
    ),
    "finance": frozenset(
        {
            "earnings",
            "revenue",
            "dividend",
            "filing",
            "10-k",
            "10-q",
            "8-k",
            "sec",
            "guidance",
            "acquisition",
            "merger",
            "ipo",
            "buyback",
            "quarterly",
        }
    ),
    "cybersecurity": frozenset(
        {
            "vulnerability",
            "cve",
            "exploit",
            "patch",
            "breach",
            "malware",
            "ransomware",
            "zero-day",
            "advisory",
            "attacker",
            "remote code execution",
            "overflow",
            "injection",
            "authentication",
        }
    ),
    "technology": frozenset(
        {
            "ai",
            "artificial intelligence",
            "machine learning",
            "model",
            "chip",
            "semiconductor",
            "cloud",
            "software",
            "hardware",
            "gpu",
            "datacenter",
        }
    ),
}


def extract_topics(text: str, *, title: str | None = None) -> list[str]:
    """Return the sorted, de-duplicated topics whose keywords appear in the title/body."""
    haystack = f"{title or ''}\n{text or ''}".lower()
    if not haystack.strip():
        return []
    matched: set[str] = set()
    for topic, keywords in _TOPIC_KEYWORDS.items():
        for kw in keywords:
            # Whole-word / phrase match so "rate" doesn't fire inside "accelerate".
            if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", haystack):
                matched.add(topic)
                break
    return sorted(matched)


__all__ = ["extract_topics"]
