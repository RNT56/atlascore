"""The alert worker (docs/mcp §3): evaluate rules → persist idempotently → deliver.

`evaluate_rules` runs each rule and upserts its candidates (idempotent on `dedupe_key`, so
re-evaluation never double-fires). `deliver_pending` sends not-yet-delivered alerts over the
configured channel and records `delivered_at`/`delivery_status` (a failed send is recorded, not
retried-forever — it stays `delivered_at=null` for the next run). `run_alerts` orchestrates both in
separate transactions; it is the control-plane entry point (`atlas alerts run`), never MCP.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from atlascore.alerts.delivery import DeliveryChannel, get_delivery_channel
from atlascore.alerts.rules import ALL_RULES, AlertRule
from atlascore.core.clock import Clock, SystemClock
from atlascore.core.logging import get_logger
from atlascore.storage.alerts import mark_delivered, undelivered_alerts, upsert_alert
from atlascore.storage.postgres.models import Alert

_log = get_logger("atlascore.worker.alerts")


@dataclass(frozen=True)
class AlertRunResult:
    created: int
    delivered: int
    failed: int


def evaluate_rules(session: Session, rules: list[AlertRule], *, now: datetime) -> int:
    """Evaluate every rule and idempotently persist its candidates; return new alerts created."""
    created = 0
    for rule in rules:
        for candidate in rule.evaluate(session, now=now):
            if upsert_alert(session, candidate, now=now):
                created += 1
    return created


def _format(alert: Alert) -> tuple[str, str]:
    subject = f"[{alert.severity}] {alert.title}"
    lines = [alert.title, f"type: {alert.alert_type}  rule: {alert.rule_id}"]
    url = (alert.detail or {}).get("canonical_url")
    if url:
        lines.append(f"evidence: {url}")
    return subject, "\n".join(lines)


def deliver_pending(
    session: Session, channel: DeliveryChannel, *, now: datetime, limit: int = 200
) -> tuple[int, int]:
    """Deliver undelivered alerts over ``channel``; return (delivered, failed)."""
    delivered = failed = 0
    for alert in undelivered_alerts(session, limit=limit):
        subject, body = _format(alert)
        try:
            status = channel.send(subject, body)
            mark_delivered(session, alert, status=status, now=now)
            delivered += 1
        except Exception as exc:  # delivery boundary: record failure, keep going (retry next run)
            _log.warning("alert_delivery_failed", alert_id=str(alert.id), error=str(exc))
            failed += 1
    return delivered, failed


def run_alerts(
    engine: Engine,
    *,
    rules: list[AlertRule] | None = None,
    channel: DeliveryChannel | None = None,
    clock: Clock | None = None,
) -> AlertRunResult:
    """Evaluate rules then deliver pending alerts. The `atlas alerts run` control-plane entry point."""
    clock = clock or SystemClock()
    rules = rules if rules is not None else ALL_RULES
    channel = channel or get_delivery_channel()

    with Session(engine) as session, session.begin():
        created = evaluate_rules(session, rules, now=clock.now())
    with Session(engine) as session, session.begin():
        delivered, failed = deliver_pending(session, channel, now=clock.now())
    return AlertRunResult(created=created, delivered=delivered, failed=failed)
