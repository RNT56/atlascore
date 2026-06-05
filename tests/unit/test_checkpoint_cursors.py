"""Unit tests for connector incremental cursors (B20; pure, offline)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlascore.connectors.nvd.cves import NvdCveConnector
from atlascore.connectors.sec.submissions import SecSubmissionsConnector
from atlascore.connectors_sdk.types import FeedEntry

pytestmark = pytest.mark.unit


def test_nvd_next_checkpoint_picks_newest_lastmodified() -> None:
    content = b"""{"vulnerabilities":[
      {"cve":{"id":"CVE-1","lastModified":"2026-05-01T00:00:00.000"}},
      {"cve":{"id":"CVE-2","lastModified":"2026-06-03T12:00:00.000"}},
      {"cve":{"id":"CVE-3","lastModified":"2026-05-15T00:00:00.000"}}
    ]}"""
    cp = NvdCveConnector().next_checkpoint(content, {})
    assert cp == {"lastModStartDate": "2026-06-03T12:00:00.000"}


def test_nvd_next_checkpoint_empty_and_malformed() -> None:
    assert NvdCveConnector().next_checkpoint(b'{"vulnerabilities": []}', {}) is None
    assert NvdCveConnector().next_checkpoint(b"not json", {}) is None


def test_sec_next_checkpoint_newest_filing() -> None:
    content = b"""{"cik":320193,"name":"Apple","filings":{"recent":{
      "accessionNumber":["acc-1","acc-2","acc-3"],
      "form":["10-K","8-K","10-Q"],
      "filingDate":["2026-01-10","2026-06-02","2026-03-15"],
      "primaryDocument":["a.htm","b.htm","c.htm"],
      "primaryDocDescription":["","",""]}}}"""
    cp = SecSubmissionsConnector().next_checkpoint(content, {})
    assert cp == {"last_filing_date": "2026-06-02", "last_accession": "acc-2"}


def test_sec_filter_entries_drops_older_keeps_same_day() -> None:
    def _e(guid: str, day: str) -> FeedEntry:
        return FeedEntry(
            guid=guid,
            link=None,
            title=guid,
            published=datetime.fromisoformat(f"{day}T00:00:00+00:00"),
        )

    entries = [_e("old", "2026-01-01"), _e("same", "2026-06-02"), _e("new", "2026-06-10")]
    kept = SecSubmissionsConnector().filter_entries(entries, {"last_filing_date": "2026-06-02"})
    assert {e.guid for e in kept} == {"same", "new"}  # same-day kept, older dropped


def test_sec_filter_entries_no_cursor_keeps_all() -> None:
    e = FeedEntry(guid="x", link=None, title="x", published=datetime(2020, 1, 1, tzinfo=UTC))
    assert SecSubmissionsConnector().filter_entries([e], {}) == [e]
