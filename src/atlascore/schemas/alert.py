"""Alert vocabulary: types, severities, lifecycle status (docs/schemas §2.10, docs/mcp §2.6).

`alert_type` is open text in the column, but the MVP rules emit exactly these values; severities
follow the docs/mcp §2.6 ordered scale (`info < low < medium < high < critical`) so `get_alerts`
can apply a `min_severity` floor.
"""

from __future__ import annotations

from enum import StrEnum


class AlertType(StrEnum):
    MACRO_RELEASE = "macro_release"
    CVE_WATCH = "cve_watch"
    SEC_FILING = "sec_filing"
    # Closes the B8/§17 seam: a source past its freshness `error_after` (source_health stale/error).
    FRESHNESS = "freshness"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    """Lifecycle: ``open → acknowledged → resolved``; ``purged`` when all evidence is erased (R3)."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    PURGED = "purged"


# Ordered severity rank for the get_alerts `min_severity` floor.
SEVERITY_ORDER: dict[str, int] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}
