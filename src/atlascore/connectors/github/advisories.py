"""GitHub Advisory Database (GHSA) connector — security advisories as documents (v1).

Fetches the **global** GitHub Advisory Database (``/advisories``, JSON) — GitHub's reviewed security
advisory feed, complementary to NVD (often earlier + ecosystem-specific). Each advisory becomes a
`FeedEntry` (GHSA id = GUID, ``html_url`` = link, summary = title, a severity/CVE-marked description
= body) so advisories are searchable via ``search_documents`` and can drive cve_watch-style alerts.

Optional filters via ``fetch.params``: ``ecosystem`` (pip/npm/…), ``severity``
(low|medium|high|critical), ``type`` (reviewed|unreviewed|malware), ``per_page``. Auth + rate tier +
ETag behave exactly like the releases connector (optional ``GITHUB_TOKEN`` Bearer header, redacted).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from atlascore.connectors_sdk.base import Connector, FetchContext
from atlascore.connectors_sdk.fetcher import SafeFetcher
from atlascore.connectors_sdk.types import FeedEntry, FetchOutcome

_API_VERSION = "2022-11-28"
_FILTER_PARAMS = ("ecosystem", "severity", "type", "cwes")


class GithubAdvisoriesConnector(Connector):
    """Fetch + parse global GitHub security advisories. Stateless; network only via the fetcher."""

    name = "github_advisories"
    accepted_mime_types = frozenset({"application/json", "text/json"})

    def fetch(self, fetcher: SafeFetcher, ctx: FetchContext) -> FetchOutcome:
        query: dict[str, str] = {"per_page": ctx.params.get("per_page", "30")}
        for key in _FILTER_PARAMS:
            value = ctx.params.get(key)
            if value:
                query[key] = value
        url = f"{ctx.feed_url.rstrip('/')}/advisories?{urlencode(query)}"
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": _API_VERSION}
        if ctx.credential:  # optional token → higher rate tier; redacted in logs/columns (§14)
            headers["Authorization"] = f"Bearer {ctx.credential}"
        return fetcher.fetch(
            url,
            etag=ctx.etag,
            last_modified=ctx.last_modified,
            timeout=ctx.timeout,
            max_requests=ctx.max_requests,
            window_seconds=ctx.window_seconds,
            accept_mime=self.accepted_mime_types,
            extra_headers=headers,
        )

    def parse_entries(self, content: bytes) -> list[FeedEntry]:
        try:
            data: Any = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"GitHub advisories response is not valid JSON: {exc}") from exc
        if not isinstance(data, list):
            raise ValueError("GitHub advisories response is not a list")

        entries: list[FeedEntry] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            ghsa_id = item.get("ghsa_id")
            if not ghsa_id:
                continue
            entries.append(
                FeedEntry(
                    guid=str(ghsa_id),
                    link=item.get("html_url"),
                    title=item.get("summary") or str(ghsa_id),
                    published=_parse_datetime(item.get("published_at")),
                    content_html=_body(item),
                )
            )
        return entries


def _body(item: dict[str, Any]) -> str | None:
    """Severity/CVE marker + summary + description (so a cve_watch-style rule can gate, like NVD)."""
    severity = str(item.get("severity") or "unknown").upper()
    cve = item.get("cve_id")
    ghsa = item.get("ghsa_id")
    marker = f"{severity} severity advisory {ghsa}" + (f" ({cve})" if cve else "") + "."
    parts = [marker, item.get("summary"), item.get("description")]
    text = "\n\n".join(p for p in parts if p)
    return text or None


def _parse_datetime(value: object) -> datetime | None:
    """Parse a GitHub ISO-8601 timestamp (e.g. ``2026-06-07T00:31:43Z``); assume UTC if naive."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
