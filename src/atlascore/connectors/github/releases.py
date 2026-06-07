"""GitHub connector — repository releases as documents (v1, docs/connectors §7.5).

Fetches a repo's releases from the GitHub REST API (``/repos/{owner}/{repo}/releases``, JSON) and
turns each into a `FeedEntry` (release id = GUID, the release ``html_url`` = link, name/tag = title,
the markdown ``body`` = document body) so releases are searchable via ``search_documents`` and can
drive release-watch alerts.

**Auth (optional, like NVD's keyed tier).** Unauthenticated GitHub allows ~60 requests/hour; a
personal token (``credentials.ref: GITHUB_TOKEN``) sent as an ``Authorization: Bearer`` **header**
raises that to ~5,000/hour. The token is a secret — it rides in a header and is redacted by
``sanitize_for_storage`` before any error/column/backup (§14). ETag is first-class (a ``304`` is a
free no-op). GitHub also requires a descriptive ``User-Agent`` (sent as ``DEFAULT_USER_AGENT``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from atlascore.connectors_sdk.base import Connector, FetchContext
from atlascore.connectors_sdk.fetcher import SafeFetcher
from atlascore.connectors_sdk.types import FeedEntry, FetchOutcome
from atlascore.core.errors import ConfigError

_API_VERSION = "2022-11-28"


class GithubReleasesConnector(Connector):
    """Fetch + parse one repo's GitHub releases. Stateless; network only via the fetcher."""

    name = "github_releases"
    accepted_mime_types = frozenset({"application/json", "text/json"})

    def fetch(self, fetcher: SafeFetcher, ctx: FetchContext) -> FetchOutcome:
        owner = ctx.params.get("owner")
        repo = ctx.params.get("repo")
        if not owner or not repo:
            raise ConfigError(
                "GitHub source missing fetch.params.owner and/or repo (e.g. owner=astral-sh, repo=ruff)"
            )
        query = urlencode({"per_page": ctx.params.get("per_page", "30")})
        url = f"{ctx.feed_url.rstrip('/')}/repos/{owner}/{repo}/releases?{query}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }
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
            raise ValueError(f"GitHub response is not valid JSON: {exc}") from exc
        if not isinstance(data, list):
            raise ValueError("GitHub releases response is not a list")

        entries: list[FeedEntry] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            release_id = item.get("id")
            if release_id is None:
                continue
            title = item.get("name") or item.get("tag_name")
            entries.append(
                FeedEntry(
                    guid=str(release_id),
                    link=item.get("html_url"),
                    title=str(title) if title else None,
                    published=_parse_datetime(item.get("published_at")),
                    content_html=item.get("body") or None,
                )
            )
        return entries


def _parse_datetime(value: object) -> datetime | None:
    """Parse a GitHub ISO-8601 timestamp (e.g. ``2026-06-04T16:34:11Z``); assume UTC if naive."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
