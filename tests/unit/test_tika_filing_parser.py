"""Unit tests for the sandboxed Tika client guard + FilingParser + SEC extraction (B22; offline).

No Java, no network: ``tika_available`` is False in this environment, so the guard path and the SEC
metadata fallback are exercised directly, and the FilingParser is driven with an injected fake Tika.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from atlascore.connectors.sec.submissions import SecSubmissionsConnector
from atlascore.connectors_sdk import SafeFetcher
from atlascore.connectors_sdk.base import FetchContext
from atlascore.core.clock import FixedClock
from atlascore.core.errors import ConfigError
from atlascore.core.settings import Settings
from atlascore.parsers.filing import PARSER_VERSION, FilingParser
from atlascore.security.sandbox.tika import TikaClient, tika_available

pytestmark = pytest.mark.unit


def test_tika_unavailable_by_default() -> None:
    assert tika_available(Settings(tika_mode="none")) is False
    # subprocess mode without a jar path is also unavailable (no crash, no Java probe).
    assert tika_available(Settings(tika_mode="subprocess", tika_jar_path=None)) is False


def test_tika_subprocess_available_requires_jar_and_java(tmp_path: Path) -> None:
    jar = tmp_path / "tika-app.jar"
    jar.write_bytes(b"not really a jar")
    s = Settings(tika_mode="subprocess", tika_jar_path=jar)
    # availability == (jar exists) AND (java on PATH). We only assert it doesn't crash + is a bool.
    assert isinstance(tika_available(s), bool)


def test_extract_raises_when_unavailable() -> None:
    with pytest.raises(ConfigError, match="Tika is not available"):
        TikaClient(Settings(tika_mode="none")).extract_text(b"%PDF-1.4 ...")


class _FakeTika:
    """A fake Tika that returns canned text with control chars + extra whitespace to be cleaned."""

    def extract_text(self, data: bytes, *, content_type: str | None = None) -> str:
        return "Item 1.\x00  Business\n\n\n\nRevenue rose.\x07   The end.   "


def test_filing_parser_cleans_extracted_text() -> None:
    parsed = FilingParser(tika=_FakeTika()).parse(b"<html>...</html>", content_type="text/html")
    assert parsed.parser_version == PARSER_VERSION
    assert (
        "\x00" not in parsed.body_text and "\x07" not in parsed.body_text
    )  # control chars stripped
    assert "Item 1. Business" in parsed.body_text  # whitespace run collapsed
    assert "\n\n\n" not in parsed.body_text  # blank-line runs collapsed
    assert parsed.char_count == len(parsed.body_text)


def _fetcher() -> SafeFetcher:
    return SafeFetcher(
        client=httpx.Client(),
        clock=FixedClock(datetime(2026, 6, 1, tzinfo=UTC)),
        resolver=lambda _host: ["93.184.216.34"],
        sleep=lambda _s: None,
        pin_connections=False,
    )


def test_sec_extract_filing_off_by_default_returns_none() -> None:
    # No opt-in flag → metadata-only fallback (None), regardless of Tika.
    ctx = FetchContext(feed_url="https://data.sec.gov", params={})
    assert SecSubmissionsConnector().extract_filing(_fetcher(), ctx, "https://x.test/f.htm") is None


def test_sec_extract_filing_optin_but_tika_absent_returns_none() -> None:
    # Opted in but Tika unavailable in this env → still None (graceful fallback, B10 behaviour).
    ctx = FetchContext(feed_url="https://data.sec.gov", params={"extract_filings": "true"})
    assert SecSubmissionsConnector().extract_filing(_fetcher(), ctx, "https://x.test/f.htm") is None


@respx.mock
def test_sec_extract_filing_optin_with_fake_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the availability gate True and inject a fake FilingParser to prove the end-to-end path.
    import atlascore.connectors.sec.submissions as sec_mod

    monkeypatch.setattr(sec_mod, "tika_available", lambda: True)
    url = "https://www.sec.gov/Archives/edgar/data/320193/0/a.htm"
    respx.get(url).mock(
        return_value=httpx.Response(
            200, content=b"<html>filing</html>", headers={"content-type": "text/html"}
        )
    )

    class _StubParser:
        def parse(self, data: bytes, *, content_type: str | None = None):
            from atlascore.parsers.filing import ParsedFiling

            return ParsedFiling(body_text="Extracted filing body.", char_count=22)

    ctx = FetchContext(feed_url="https://data.sec.gov", params={"extract_filings": "true"})
    body = SecSubmissionsConnector().extract_filing(
        _fetcher(), ctx, url, filing_parser=_StubParser()
    )
    assert body == "Extracted filing body."
