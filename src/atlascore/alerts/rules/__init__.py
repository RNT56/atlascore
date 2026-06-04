"""Alert rules (docs/mcp §3, CLAUDE.md §11.10). ``ALL_RULES`` is the MVP rule set."""

from __future__ import annotations

from atlascore.alerts.rules.base import AlertCandidate, AlertRule
from atlascore.alerts.rules.documents import CveWatchRule, SecFilingRule
from atlascore.alerts.rules.freshness import FreshnessRule
from atlascore.alerts.rules.macro import MacroReleaseRule

# The MVP rule set evaluated by the alert job (the three content rules + the freshness rule).
ALL_RULES: list[AlertRule] = [
    SecFilingRule(),
    CveWatchRule(),
    MacroReleaseRule(),
    FreshnessRule(),
]

__all__ = [
    "ALL_RULES",
    "AlertCandidate",
    "AlertRule",
    "CveWatchRule",
    "FreshnessRule",
    "MacroReleaseRule",
    "SecFilingRule",
]
