"""NVD (National Vulnerability Database) connector — CVEs as documents (docs/connectors §7.4).

Fetches the public NVD CVE 2.0 JSON API and turns each vulnerability into a `FeedEntry` (CVE id =
GUID, the NVD detail URL = link, the English description = body) so CVEs are searchable via
``search_documents`` (B10). Optional ``params``: ``keyword`` (``keywordSearch``) and
``results_per_page`` (``resultsPerPage``).

**Keyless tier (B10):** NVD allows ~5 requests / 30 s without a key; the source's `rate_limit`
reflects that. The keyed tier sends the key as an ``apiKey`` **header** for a higher limit — that
needs custom-header support in the fetcher and is a documented follow-up; B10 runs keyless.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from atlascore.connectors_sdk.base import Connector, FetchContext
from atlascore.connectors_sdk.fetcher import SafeFetcher
from atlascore.connectors_sdk.types import FeedEntry, FetchOutcome

_CVES_PATH = "/rest/json/cves/2.0"
_DETAIL_BASE = "https://nvd.nist.gov/vuln/detail"


class NvdCveConnector(Connector):
    """Fetch + parse recent/matching NVD CVEs. Stateless; network only via the fetcher."""

    name = "nvd_cve"
    accepted_mime_types = frozenset({"application/json", "text/json"})

    def fetch(self, fetcher: SafeFetcher, ctx: FetchContext) -> FetchOutcome:
        query: dict[str, str] = {}
        keyword = ctx.params.get("keyword")
        if keyword:
            query["keywordSearch"] = keyword
        per_page = ctx.params.get("results_per_page")
        if per_page:
            query["resultsPerPage"] = per_page
        url = f"{ctx.feed_url.rstrip('/')}{_CVES_PATH}"
        if query:
            url = f"{url}?{urlencode(query)}"
        return fetcher.fetch(
            url,
            etag=ctx.etag,
            last_modified=ctx.last_modified,
            timeout=ctx.timeout,
            max_requests=ctx.max_requests,
            window_seconds=ctx.window_seconds,
            accept_mime=self.accepted_mime_types,
        )

    def parse_entries(self, content: bytes) -> list[FeedEntry]:
        try:
            data: Any = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"NVD response is not valid JSON: {exc}") from exc
        if not isinstance(data, dict) or "vulnerabilities" not in data:
            raise ValueError("NVD response missing 'vulnerabilities'")
        vulnerabilities = data.get("vulnerabilities")
        if not isinstance(vulnerabilities, list):
            raise ValueError("NVD 'vulnerabilities' is not a list")

        entries: list[FeedEntry] = []
        for item in vulnerabilities:
            cve = (item or {}).get("cve") if isinstance(item, dict) else None
            if not isinstance(cve, dict):
                continue
            cve_id = cve.get("id")
            if not cve_id:
                continue
            description = _english_description(cve.get("descriptions"))
            entries.append(
                FeedEntry(
                    guid=str(cve_id),
                    link=f"{_DETAIL_BASE}/{cve_id}",
                    title=str(cve_id),
                    published=_parse_datetime(cve.get("published")),
                    content_html=description,
                )
            )
        return entries


def _english_description(descriptions: object) -> str | None:
    if not isinstance(descriptions, list):
        return None
    english = [
        d.get("value")
        for d in descriptions
        if isinstance(d, dict) and d.get("lang") == "en" and d.get("value")
    ]
    if english:
        return str(english[0])
    first = next(
        (d.get("value") for d in descriptions if isinstance(d, dict) and d.get("value")), None
    )
    return str(first) if first else None


def _parse_datetime(value: object) -> datetime | None:
    """Parse an NVD ISO timestamp (e.g. ``2026-05-01T00:00:00.000``); assume UTC if naive."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
