"""SEC EDGAR submissions connector — a company's recent filings as documents (docs/connectors §7.3).

Fetches the public ``/submissions/CIK##########.json`` endpoint for one company (CIK from
``source.config.params.cik``) and turns each recent filing into a `FeedEntry` (accession number =
GUID, the primary-document URL = link, form + company = title, a short description = body). The
shared `ArticleParser` then produces the sanitized `document` body, so filings are searchable via
``search_documents``.

**Full-filing extraction (B22, opt-in).** When the source sets ``fetch.params.extract_filings:
'true'`` AND a sandboxed Tika is available (``TIKA_MODE``), :meth:`extract_filing` fetches a filing's
primary document (PDF/HTML) through the same SSRF-guarded fetcher and runs it through the sandboxed
:class:`FilingParser` to produce a full-text body. When Tika is absent or the flag is off it returns
``None`` and the connector falls back to the metadata-only body (exact B10 behaviour) — so CI and the
sandbox stay green without Java.

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
from atlascore.core.errors import ConfigError, FetchError, SecurityError, SsrfError
from atlascore.core.logging import get_logger
from atlascore.parsers.filing import FilingParser
from atlascore.security.sandbox import tika_available

# The filing archive lives on www.sec.gov; the submissions JSON on data.sec.gov (the base_url).
_ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"
# MIME types accepted when fetching a filing's primary document for Tika extraction (B22).
_FILING_MIME = frozenset({"application/pdf", "text/html", "application/xhtml+xml", "text/plain"})

_log = get_logger("atlascore.connectors.sec")


class SecSubmissionsConnector(Connector):
    """Fetch + parse one company's recent SEC filings. Stateless; network only via the fetcher."""

    name = "sec_submissions"
    accepted_mime_types = frozenset({"application/json", "text/json"})

    def extract_filing(
        self,
        fetcher: SafeFetcher,
        ctx: FetchContext,
        url: str,
        *,
        filing_parser: FilingParser | None = None,
    ) -> str | None:
        """Full-text-extract a filing's primary doc via sandboxed Tika; ``None`` to fall back (B22).

        Returns ``None`` (metadata-only fallback) when the source hasn't opted in
        (``params.extract_filings != 'true'``), when Tika isn't available, or when the
        fetch/extraction fails (never propagates — a bad filing must not fail the whole crawl).
        """
        if ctx.params.get("extract_filings") != "true" or not tika_available():
            return None
        try:
            outcome = fetcher.fetch(
                url,
                timeout=ctx.timeout,
                max_requests=ctx.max_requests,
                window_seconds=ctx.window_seconds,
                accept_mime=_FILING_MIME,
            )
            if outcome.not_modified or not outcome.content:
                return None
            parsed = (filing_parser or FilingParser()).parse(
                outcome.content, content_type=outcome.mime_type
            )
            return parsed.body_text or None
        except (FetchError, SsrfError, SecurityError, ConfigError) as exc:
            _log.warning("sec_filing_extract_failed", url=url, error=str(exc))
            return None

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

    def filter_entries(
        self, entries: list[FeedEntry], checkpoint: dict[str, Any]
    ) -> list[FeedEntry]:
        """Skip filings strictly older than the cursor (B20 optimisation; dedupe stays correctness).

        Keeps same-day filings (>= cursor date) so an unseen filing filed on the cursor's own day is
        never missed; the parse-job dedupe_key drops the already-seen ones. Entries with no date are
        kept (can't compare). The full ``recent`` array is always re-fetched (SEC has no date param);
        this only trims how many *stale* parse jobs we re-enqueue each tick.
        """
        cursor = checkpoint.get("last_filing_date")
        if not cursor:
            return entries
        kept = []
        for e in entries:
            if e.published is None or e.published.date().isoformat() >= cursor:
                kept.append(e)
        return kept

    def next_checkpoint(self, content: bytes, prior: dict[str, Any]) -> dict[str, Any] | None:
        """Record the newest filingDate (+ its accession) seen, for the next tick's filter."""
        try:
            data = json.loads(content)
            recent = (data.get("filings") or {}).get("recent") or {}
        except (json.JSONDecodeError, AttributeError):
            return None
        dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        if not dates:
            return None
        newest_idx = max(range(len(dates)), key=lambda i: dates[i] or "")
        newest_date = dates[newest_idx]
        if not newest_date:
            return None
        cp: dict[str, Any] = {"last_filing_date": newest_date}
        if newest_idx < len(accessions):
            cp["last_accession"] = accessions[newest_idx]
        return cp


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
