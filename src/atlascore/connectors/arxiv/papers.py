"""arXiv connector — research papers as documents (v1, docs/connectors §7.6).

The arXiv export API (``/api/query``) returns **Atom**, which is exactly what the generic
:class:`RssConnector` already parses via feedparser — so this connector only overrides ``fetch`` to
build the query URL and inherits ``parse_entries`` (each entry's ``<id>`` ``http://arxiv.org/abs/…``
is the GUID, ``<summary>`` the abstract → document body). Keyless; be gentle (arXiv asks for ≤ ~1
request / 3 s).

Series/query is addressed by ``fetch.params.search_query`` (arXiv query syntax, e.g. ``cat:cs.LG``);
optional ``max_results`` / ``start`` / ``sort_by`` / ``sort_order`` tune the page.
"""

from __future__ import annotations

from urllib.parse import urlencode

from atlascore.connectors.rss.feed import RssConnector
from atlascore.connectors_sdk.base import FetchContext
from atlascore.connectors_sdk.fetcher import SafeFetcher
from atlascore.connectors_sdk.types import FetchOutcome
from atlascore.core.errors import ConfigError

_QUERY_PATH = "/api/query"


class ArxivConnector(RssConnector):
    """Fetch + parse one arXiv query. Inherits feedparser parsing; only the URL build differs."""

    name = "arxiv"

    def fetch(self, fetcher: SafeFetcher, ctx: FetchContext) -> FetchOutcome:
        search_query = ctx.params.get("search_query")
        if not search_query:
            raise ConfigError("arXiv source missing fetch.params.search_query (e.g. 'cat:cs.LG')")
        query = {
            "search_query": search_query,
            "start": ctx.params.get("start", "0"),
            "max_results": ctx.params.get("max_results", "50"),
            "sortBy": ctx.params.get("sort_by", "submittedDate"),
            "sortOrder": ctx.params.get("sort_order", "descending"),
        }
        url = f"{ctx.feed_url.rstrip('/')}{_QUERY_PATH}?{urlencode(query)}"
        return fetcher.fetch(
            url,
            etag=ctx.etag,
            last_modified=ctx.last_modified,
            timeout=ctx.timeout,
            max_requests=ctx.max_requests,
            window_seconds=ctx.window_seconds,
            accept_mime=self.accepted_mime_types,
        )
