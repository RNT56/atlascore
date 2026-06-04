"""SEC EDGAR submissions connector — a company's recent filings as documents (docs/connectors §7.3).

Fetches the public ``/submissions/CIK##########.json`` endpoint for one company (CIK from
``source.config.params.cik``) and turns each recent filing into a `FeedEntry` (accession number =
GUID, the primary-document URL = link, form + company = title, a short description = body). The
shared `ArticleParser` then produces the sanitized `document` body, so filings are searchable via
``search_documents`` (B10 "basic"; full filing-document extraction via Tika is out of scope).

SEC requires a descriptive **User-Agent with a contact** (sent as ``DEFAULT_USER_AGENT``) and a
**10 req/s** limit (the source's `rate_limit`, enforced by the single fetch worker). No API key.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from atlascore.connectors_sdk.base import Connector, FetchContext
from atlascore.connectors_sdk.fetcher import SafeFetcher
from atlascore.connectors_sdk.types import FeedEntry, FetchOutcome
from atlascore.core.errors import ConfigError

# The filing archive lives on www.sec.gov; the submissions JSON on data.sec.gov (the base_url).
_ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"


class SecSubmissionsConnector(Connector):
    """Fetch + parse one company's recent SEC filings. Stateless; network only via the fetcher."""

    name = "sec_submissions"
    accepted_mime_types = frozenset({"application/json", "text/json"})

    def fetch(self, fetcher: SafeFetcher, ctx: FetchContext) -> FetchOutcome:
        cik = ctx.params.get("cik")
        if not cik:
            raise ConfigError("SEC source missing fetch.params.cik")
        cik_padded = cik.strip().lstrip("0").zfill(10)
        url = f"{ctx.feed_url.rstrip('/')}/submissions/CIK{cik_padded}.json"
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
            raise ValueError(f"SEC submissions response is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("SEC submissions response is not an object")
        name = str(data.get("name") or "")
        cik = str(data.get("cik") or "").lstrip("0")
        recent = (data.get("filings") or {}).get("recent") or {}
        if "accessionNumber" not in recent:
            raise ValueError("SEC submissions response missing filings.recent.accessionNumber")

        accession = recent.get("accessionNumber") or []
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        primary = recent.get("primaryDocument") or []
        descriptions = recent.get("primaryDocDescription") or []

        entries: list[FeedEntry] = []
        for i, acc in enumerate(accession):
            form = forms[i] if i < len(forms) else ""
            filing_date = dates[i] if i < len(dates) else None
            primary_doc = primary[i] if i < len(primary) else ""
            desc = descriptions[i] if i < len(descriptions) else ""
            published = _parse_date(filing_date)
            link = _filing_url(cik, acc, primary_doc)
            label = (desc or form or "Filing").strip()
            body = f"{label} (form {form}) filed by {name} (CIK {cik}) on {filing_date}."
            entries.append(
                FeedEntry(
                    guid=str(acc),
                    link=link,
                    title=f"{form} — {name}" if form and name else (form or name or str(acc)),
                    published=published,
                    content_html=body,
                )
            )
        return entries


def _filing_url(cik: str, accession: str, primary_doc: str) -> str | None:
    if not cik or not accession:
        return None
    no_dashes = accession.replace("-", "")
    base = f"{_ARCHIVE_BASE}/{cik}/{no_dashes}"
    return f"{base}/{primary_doc}" if primary_doc else f"{base}/"


def _parse_date(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None
