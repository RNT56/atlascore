"""Unit/contract tests for the NVD CVE connector (offline; respx-mocked)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from atlascore.connectors.nvd.cves import NvdCveConnector
from atlascore.connectors_sdk import FetchContext, OutputKind, SafeFetcher
from atlascore.core.clock import FixedClock

pytestmark = pytest.mark.unit

CVES = (Path(__file__).parent.parent / "fixtures" / "nvd" / "cves_sample.json").read_bytes()
NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


def _fetcher() -> SafeFetcher:
    return SafeFetcher(
        client=httpx.Client(),
        clock=FixedClock(NOW),
        resolver=lambda _host: ["93.184.216.34"],
        sleep=lambda _s: None,
        pin_connections=False,
    )


def test_output_kind_is_document() -> None:
    assert NvdCveConnector.output_kind is OutputKind.DOCUMENT


def test_parse_entries_golden() -> None:
    entries = NvdCveConnector().parse_entries(CVES)
    assert len(entries) == 2
    first = entries[0]
    assert first.guid == "CVE-2026-10001"
    assert first.title == "CVE-2026-10001"
    assert first.link == "https://nvd.nist.gov/vuln/detail/CVE-2026-10001"
    assert first.published == datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    assert first.content_html is not None
    assert "buffer overflow" in first.content_html  # English description chosen...
    assert "desbordamiento" not in first.content_html  # ...not the Spanish one


@respx.mock
def test_fetch_with_keyword_param() -> None:
    route = respx.get(url__regex=r"https://services\.nvd\.nist\.gov/rest/json/cves.*").mock(
        return_value=httpx.Response(200, content=CVES, headers={"content-type": "application/json"})
    )
    ctx = FetchContext(feed_url="https://services.nvd.nist.gov", params={"keyword": "apache"})
    outcome = NvdCveConnector().fetch(_fetcher(), ctx)
    assert outcome.status == 200
    assert "keywordSearch=apache" in str(route.calls[0].request.url)


def test_parse_missing_vulnerabilities_raises() -> None:
    with pytest.raises(ValueError, match="vulnerabilities"):
        NvdCveConnector().parse_entries(b'{"format": "NVD_CVE"}')


def test_parse_not_json_raises() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        NvdCveConnector().parse_entries(b"not json at all")
