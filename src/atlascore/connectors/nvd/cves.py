"""NVD (National Vulnerability Database) connector — CVEs as documents (docs/connectors §7.4).

Fetches the public NVD CVE 2.0 JSON API and turns each vulnerability into a `FeedEntry` (CVE id =
GUID, the NVD detail URL = link, the English description = body) so CVEs are searchable via
``search_documents`` (B10). Optional ``params``: ``keyword`` (``keywordSearch``) and
``results_per_page`` (``resultsPerPage``).

**NVD tiers:** the keyless source runs conservatively at ~5 requests / 30 s. B20 also ships the
keyed tier: when ``ctx.credential`` is set (usually ``credential_ref: NVD_API_KEY``), the connector
sends it as the ``apiKey`` **header** for the higher-rate tier. B20 also adds the runtime
``source.checkpoint`` cursor for ``lastModStartDate`` / ``lastModEndDate`` incremental windows.
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
        # Incremental window (B20): NVD requires lastModStartDate + lastModEndDate together; a
        # startIndex pages within it. Carried in the runtime checkpoint (never YAML).
        cp = ctx.checkpoint or {}
        start = cp.get("lastModStartDate")
        # NVD requires lastModStartDate + lastModEndDate together; end = the worker's fetch-time now.
        if start and ctx.now is not None:
            query["lastModStartDate"] = str(start)
            query["lastModEndDate"] = ctx.now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000")
        if cp.get("startIndex"):
            query["startIndex"] = str(cp["startIndex"])
        url = f"{ctx.feed_url.rstrip('/')}{_CVES_PATH}"
        if query:
            url = f"{url}?{urlencode(query)}"
        # Keyed tier (B20): send the resolved credential as the `apiKey` HEADER (NIST higher-rate
        # tier ~50 req/30s). Keyless stays default. The value is never logged (sanitize_for_storage).
        headers = {"apiKey": ctx.credential} if ctx.credential else None
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

    def next_checkpoint(self, content: bytes, prior: dict[str, Any]) -> dict[str, Any] | None:
        """Record the newest ``lastModified`` seen as the next window's start (B20).

        Returns ``{lastModStartDate, lastModEndDate}`` so the next fetch only asks for CVEs modified
        after this run. Returns None on an empty/unparseable payload (leave the cursor untouched).
        """
        try:
            data = json.loads(content)
            vulns = data.get("vulnerabilities") if isinstance(data, dict) else None
        except (json.JSONDecodeError, AttributeError):
            return None
        if not isinstance(vulns, list) or not vulns:
            return None
        newest = ""
        for item in vulns:
            cve = (item or {}).get("cve") if isinstance(item, dict) else None
            mod = cve.get("lastModified") if isinstance(cve, dict) else None
            if isinstance(mod, str) and mod > newest:
                newest = mod
        if not newest:
            return None
        # NVD's window is inclusive; next start = newest seen. End is left to the worker's "now"
        # at fetch time — we store start; the connector fills end from start..now on the next run.
        return {"lastModStartDate": newest}

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
            # Surface CVSS into the body as a parser-safe marker so the cve_watch rule (B18) can gate
            # on it without a schema change — the score/severity otherwise never reach the document.
            score, severity = _cvss(cve)
            body: str | None = description
            if score is not None:
                marker = f"CVSS base score {score} ({severity or _band(score)})."
                body = f"{marker} {description}" if description else marker
            entries.append(
                FeedEntry(
                    guid=str(cve_id),
                    link=f"{_DETAIL_BASE}/{cve_id}",
                    title=str(cve_id),
                    published=_parse_datetime(cve.get("published")),
                    content_html=body,
                )
            )
        return entries


def _cvss(cve: dict[str, Any]) -> tuple[float | None, str | None]:
    """Extract (baseScore, baseSeverity) from CVSS v3.1 → v3.0 → v2 (severity may be derived)."""
    metrics = cve.get("metrics")
    if not isinstance(metrics, dict):
        return None, None
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        entries = metrics.get(key)
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            data = entries[0].get("cvssData")
            if isinstance(data, dict) and isinstance(data.get("baseScore"), int | float):
                score = float(data["baseScore"])
                sev = data.get("baseSeverity")
                return score, str(sev).upper() if isinstance(sev, str) else _band(score)
    return None, None


def _band(score: float) -> str:
    """NVD qualitative band for a base score (used when baseSeverity is absent, e.g. CVSS v2)."""
    if score >= 9.0:
        return "CRITICAL"
    if score >= 7.0:
        return "HIGH"
    if score >= 4.0:
        return "MEDIUM"
    return "LOW"


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
