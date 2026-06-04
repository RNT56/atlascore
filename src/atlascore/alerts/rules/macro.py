"""The `macro_release` alert rule (FRED time-series).

Fires once per macro series when it posts a **new latest observation** — the ``dedupe_key`` includes
the observation ``ts``, so a fresh release (new ``ts``) fires once and a re-run never re-fires.
Macro observations are not documents, so ``evidence_doc_ids`` is empty and the evidence (provider,
series, ts, value, unit) lives in ``detail``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlascore.alerts.rules.base import AlertCandidate
from atlascore.schemas.alert import AlertType, Severity
from atlascore.storage.postgres.models import TimeSeriesPoint

_SCAN_LIMIT = 500


class MacroReleaseRule:
    """Fire a `macro_release` alert for the latest observation of each FRED series."""

    rule_id = "macro_release_new"

    def evaluate(self, session: Session, *, now: datetime) -> list[AlertCandidate]:
        # DISTINCT ON (provider, series_id) ordered by ts DESC → the latest point per series.
        stmt = (
            select(TimeSeriesPoint)
            .where(TimeSeriesPoint.kind == "observation")
            .order_by(
                TimeSeriesPoint.provider,
                TimeSeriesPoint.series_id,
                TimeSeriesPoint.ts.desc(),
            )
            .distinct(TimeSeriesPoint.provider, TimeSeriesPoint.series_id)
            .limit(_SCAN_LIMIT)
        )
        candidates = []
        for point in session.scalars(stmt):
            ts_iso = point.ts.isoformat()
            candidates.append(
                AlertCandidate(
                    alert_type=AlertType.MACRO_RELEASE.value,
                    rule_id=self.rule_id,
                    severity=Severity.INFO.value,
                    title=f"{point.provider}:{point.series_id} new observation {point.ts.date()}",
                    dedupe_key=f"{self.rule_id}:{point.provider}:{point.series_id}:{ts_iso}",
                    evidence_doc_ids=[],
                    detail={
                        "provider": point.provider,
                        "series_id": point.series_id,
                        "ts": ts_iso,
                        "value": str(point.value) if point.value is not None else None,
                        "unit": point.unit,
                    },
                )
            )
        return candidates
