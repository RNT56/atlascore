"""The `freshness` alert rule — closes the B8/§17 seam.

B8 computes `source_health`; §17 says a source past its `error_after` (status `stale`/`error`, or a
circuit-broken `cooling_down`) should fire a freshness alert. This rule reads `source_health` and
emits one alert per source currently in a bad state. `warn` stays digest-only (not an alert).

The `dedupe_key` is keyed on `(source, status)`, so entering a bad state fires once; it does not
re-fire while the source stays in that same state (documented MVP limitation — a recovery then a
re-break into the *same* status won't re-alert until the dedupe row is pruned).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from atlascore.alerts.rules.base import AlertCandidate
from atlascore.schemas.alert import AlertType, Severity
from atlascore.schemas.source_health import HealthStatus
from atlascore.storage.source_health import list_source_health

# Bad states that warrant an alert (warn is digest-only).
_ALERTING_STATUSES = [
    HealthStatus.STALE.value,
    HealthStatus.ERROR.value,
    HealthStatus.COOLING_DOWN.value,
]
_SEVERITY = {
    HealthStatus.STALE.value: Severity.HIGH.value,
    HealthStatus.ERROR.value: Severity.HIGH.value,
    HealthStatus.COOLING_DOWN.value: Severity.MEDIUM.value,
}


class FreshnessRule:
    """Fire a `freshness` alert for each source in a stale/error/cooling_down state."""

    rule_id = "freshness_stale"

    def evaluate(self, session: Session, *, now: datetime) -> list[AlertCandidate]:
        candidates = []
        for row in list_source_health(session, statuses=_ALERTING_STATUSES):
            lag = row.freshness_lag
            candidates.append(
                AlertCandidate(
                    alert_type=AlertType.FRESHNESS.value,
                    rule_id=self.rule_id,
                    severity=_SEVERITY.get(row.status, Severity.HIGH.value),
                    title=f"Source {row.source_slug or row.source_name} is {row.status}",
                    dedupe_key=f"{self.rule_id}:{row.source_id}:{row.status}",
                    evidence_doc_ids=[],
                    detail={
                        "source": row.source_name,
                        "source_slug": row.source_slug,
                        "status": row.status,
                        "freshness_lag_seconds": int(lag.total_seconds())
                        if lag is not None
                        else None,
                        "consecutive_failures": row.consecutive_failures,
                    },
                )
            )
        return candidates
