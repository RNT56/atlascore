"""Alert rules (docs/mcp §3, CLAUDE.md §11.10).

``build_rules(watchlist)`` is the factory the worker uses so the document rules can be watchlist-gated
(B18); ``ALL_RULES`` stays as a backward-compatible module constant (= ``build_rules(None)``,
fire-on-everything) for existing imports/tests.
"""

from __future__ import annotations

from atlascore.alerts.rules.base import AlertCandidate, AlertRule
from atlascore.alerts.rules.documents import CveWatchRule, SecFilingRule
from atlascore.alerts.rules.freshness import FreshnessRule
from atlascore.alerts.rules.macro import MacroReleaseRule
from atlascore.schemas.watchlist import Watchlist


def build_rules(watchlist: Watchlist | None = None) -> list[AlertRule]:
    """The MVP rule set, with the document rules gated by ``watchlist`` (None ⇒ fire on everything)."""
    return [
        SecFilingRule(watchlist=watchlist),
        CveWatchRule(watchlist=watchlist),
        MacroReleaseRule(),
        FreshnessRule(),
    ]


# Backward-compatible ungated rule set (used by tests that inject rules explicitly).
ALL_RULES: list[AlertRule] = build_rules(None)

__all__ = [
    "ALL_RULES",
    "AlertCandidate",
    "AlertRule",
    "CveWatchRule",
    "FreshnessRule",
    "MacroReleaseRule",
    "SecFilingRule",
    "build_rules",
]
