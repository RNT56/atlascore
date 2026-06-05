"""Unit tests for CVSS extraction (NVD connector + cve_watch rule) and watchlist gating (offline)."""

from __future__ import annotations

import pytest

from atlascore.alerts.rules.documents import (
    CveWatchRule,
    _cvss_to_severity,
    _extract_cvss,
)
from atlascore.connectors.nvd.cves import NvdCveConnector, _cvss
from atlascore.schemas.watchlist import Watchlist
from atlascore.storage.postgres.models import Document

pytestmark = pytest.mark.unit


def test_connector_cvss_v31_v30_v2_and_missing() -> None:
    assert _cvss(
        {
            "metrics": {
                "cvssMetricV31": [{"cvssData": {"baseScore": 9.8, "baseSeverity": "CRITICAL"}}]
            }
        }
    ) == (9.8, "CRITICAL")
    assert _cvss(
        {"metrics": {"cvssMetricV30": [{"cvssData": {"baseScore": 7.5, "baseSeverity": "HIGH"}}]}}
    ) == (7.5, "HIGH")
    # v2 has no baseSeverity → derived from band.
    assert _cvss({"metrics": {"cvssMetricV2": [{"cvssData": {"baseScore": 5.0}}]}}) == (
        5.0,
        "MEDIUM",
    )
    assert _cvss({"metrics": {}}) == (None, None)
    assert _cvss({}) == (None, None)


def test_nvd_parse_prepends_cvss_marker() -> None:
    content = b"""{"vulnerabilities":[{"cve":{"id":"CVE-2026-1","published":"2026-06-01T00:00:00.000",
      "descriptions":[{"lang":"en","value":"A buffer overflow."}],
      "metrics":{"cvssMetricV31":[{"cvssData":{"baseScore":9.8,"baseSeverity":"CRITICAL"}}]}}}]}"""
    entries = NvdCveConnector().parse_entries(content)
    assert entries[0].content_html == "CVSS base score 9.8 (CRITICAL). A buffer overflow."


def test_nvd_parse_no_metrics_description_only() -> None:
    content = b"""{"vulnerabilities":[{"cve":{"id":"CVE-2026-2","published":"2026-06-01T00:00:00.000",
      "descriptions":[{"lang":"en","value":"Minor issue."}]}}]}"""
    entries = NvdCveConnector().parse_entries(content)
    assert entries[0].content_html == "Minor issue."


def test_extract_cvss_round_trips_marker() -> None:
    assert _extract_cvss("CVSS base score 8.1 (HIGH). details") == (8.1, "HIGH")
    assert _extract_cvss("no marker here") == (None, None)
    assert _extract_cvss(None) == (None, None)


def test_cvss_to_severity_bands() -> None:
    assert _cvss_to_severity(9.8) == "critical"
    assert _cvss_to_severity(8.1) == "high"
    assert _cvss_to_severity(5.0) == "medium"
    assert _cvss_to_severity(1.0) == "low"
    assert _cvss_to_severity(0.0) == "info"
    assert _cvss_to_severity(None) == "medium"


def _doc(title: str, body: str) -> Document:
    return Document(title=title, body_text=body)  # transient ORM object (no session)


def test_cve_watch_matches_gating() -> None:
    wl = Watchlist.model_validate(
        {
            "cve": {
                "min_cvss": 9.0,
                "severities": ["CRITICAL"],
                "cve_ids": ["CVE-2026-9"],
                "keywords": ["acme"],
            }
        }
    )
    rule = CveWatchRule(watchlist=wl)
    crit = _doc("CVE-2026-1", "CVSS base score 9.8 (CRITICAL). x")
    assert rule._matches(wl, score=9.8, severity="CRITICAL", doc=crit) == "min_cvss"
    medium = _doc("CVE-2026-2", "CVSS base score 5.0 (MEDIUM). x")
    assert (
        rule._matches(wl, score=5.0, severity="MEDIUM", doc=medium) is None
    )  # below floor, not watched
    by_id = _doc("CVE-2026-9", "no score")
    assert rule._matches(wl, score=None, severity=None, doc=by_id) == "cve_id"
    by_kw = _doc("CVE-2026-3", "an ACME gateway flaw")
    assert rule._matches(wl, score=None, severity=None, doc=by_kw) == "keyword"
