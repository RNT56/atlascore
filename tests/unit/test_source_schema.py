"""Unit tests for the canonical ``source`` record + default-deny policy."""

from __future__ import annotations

import pytest

from atlascore.schemas.source import AccessMethod, PolicyStatus, SourceRecord

pytestmark = pytest.mark.unit

# The docs/schemas §8 source row, as mirrored into the `source` table (config jsonb
# holds allowed_uses/robots/connector/schedule/timeout_seconds — round-trip home R9).
CANONICAL_SOURCE = {
    "name": "SEC EDGAR Company Facts",
    "category": "finance_filings",
    "access_method": "api",
    "base_url": "https://data.sec.gov",
    "policy_status": "approved",
    "rate_limit": {"max_requests": 10, "window_seconds": 1},
    "retention": {"raw": "2y", "normalized": "permanent"},
    "freshness": {"target": "24h", "warn_after": "36h", "error_after": "72h"},
    "config": {
        "allowed_uses": ["internal_search", "internal_summary", "agent_retrieval"],
        "robots": "respect",
        "connector": "atlascore.connectors.sec.companyfacts:SecCompanyFactsConnector",
        "schedule": "*/15 * * * *",
        "timeout_seconds": 30,
    },
    "parser_ref": "atlascore.parsers.filing:FilingParser",
    "credential_ref": None,
    "enabled": True,
    "schema_version": 1,
}


def test_parses_canonical_source() -> None:
    rec = SourceRecord.model_validate(CANONICAL_SOURCE)
    assert rec.access_method is AccessMethod.API
    assert rec.policy_status is PolicyStatus.APPROVED
    assert rec.config.schedule == "*/15 * * * *"
    assert rec.config.connector is not None
    assert rec.rate_limit is not None
    assert rec.rate_limit.max_requests == 10
    assert rec.credential_ref is None  # never a secret value
    assert rec.is_fetchable is True


def test_round_trips_losslessly() -> None:
    rec = SourceRecord.model_validate(CANONICAL_SOURCE)
    reloaded = SourceRecord.model_validate(rec.model_dump(mode="json"))
    assert reloaded == rec


def test_default_deny_defaults() -> None:
    rec = SourceRecord(name="x", category="news_rss", access_method=AccessMethod.RSS)
    assert rec.policy_status is PolicyStatus.UNKNOWN
    assert rec.enabled is False
    assert rec.is_fetchable is False


@pytest.mark.parametrize(
    ("status", "fetchable"),
    [
        (PolicyStatus.APPROVED, True),
        (PolicyStatus.RESTRICTED, True),
        (PolicyStatus.PENDING, False),
        (PolicyStatus.UNKNOWN, False),
        (PolicyStatus.PROHIBITED, False),
    ],
)
def test_policy_status_is_fetchable(status: PolicyStatus, fetchable: bool) -> None:
    assert status.is_fetchable is fetchable


def test_enabled_but_not_approved_is_not_fetchable() -> None:
    rec = SourceRecord(
        name="x",
        category="news_rss",
        access_method=AccessMethod.RSS,
        enabled=True,
        policy_status=PolicyStatus.PENDING,
    )
    assert rec.is_fetchable is False


def test_rejects_unknown_access_method() -> None:
    with pytest.raises(ValueError, match="access_method"):
        SourceRecord(name="x", category="c", access_method="ftp")  # type: ignore[arg-type]
