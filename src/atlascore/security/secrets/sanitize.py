"""``sanitize_for_storage`` — redact secrets before they reach a DB column or backup.

Spec invariant (CLAUDE.md §14, docs/security): the value written to
``crawl_job.last_error``, ``crawl_job.payload``, or ``audit_log.detail`` MUST first pass
through this single helper. It:

- strips URL **query** strings and **userinfo** (FRED uses ``?api_key=…``);
- redacts ``Authorization`` / ``Cookie`` / ``Proxy-Authorization`` header lines and
  ``Bearer`` tokens;
- redacts ``key=value`` / ``key: value`` pairs whose key looks like a credential;
- redacts ``dict`` values whose **key** looks like a credential (recursively).

Backups inherit this because sanitization happens at write time, not log time. The B15
secrets-in-logs regression test exercises it against `last_error` / `audit_log.detail`.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

REDACTED = "[REDACTED]"

# Credential-looking tokens used to match dict keys and key=value pairs. Deliberately
# excludes bare "auth"/"key" (they false-match "author"/"monkey"); "Authorization"/"Cookie"
# headers are handled by _HEADER_RE instead.
_CRED_KEY_FRAGMENT = (
    r"api[_-]?key|apikey|x[_-]?api[_-]?key|access[_-]?token|refresh[_-]?token"
    r"|client[_-]?secret|secret|password|passwd|pwd|token|private[_-]?key|credential"
)

_SECRET_KEY_RE = re.compile(rf"(?i)(?:{_CRED_KEY_FRAGMENT}|authorization|cookie)")
_URL_RE = re.compile(r"(?i)\bhttps?://[^\s'\"<>\\]+")
# Match key=value / key: value where the key looks like a credential. Quote-aware on BOTH
# sides so JSON bodies and dict/Headers reprs ({"api_key":"X"}, {'apiKey': 'X'}) are caught —
# group 1 (key + optional quotes + separator) is kept, the value (group 2) is redacted.
_KV_RE = re.compile(
    rf"(?i)([\"']?[\w\-]*(?:{_CRED_KEY_FRAGMENT})[\w\-]*[\"']?\s*[:=]\s*)"
    r"([\"'][^\"']*[\"']|[^\s,;&'\"]+)"
)
_HEADER_RE = re.compile(
    r"(?im)^(\s*(?:authorization|cookie|set-cookie|proxy-authorization|apikey|x-api-key)\s*:\s*).+$"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")


def _sanitize_url(match: re.Match[str]) -> str:
    parts = urlsplit(match.group(0))
    if not parts.query and "@" not in parts.netloc:
        return match.group(0)  # nothing sensitive to strip
    netloc = parts.hostname or ""
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _sanitize_str(text: str) -> str:
    text = _URL_RE.sub(_sanitize_url, text)
    text = _HEADER_RE.sub(lambda m: m.group(1) + REDACTED, text)
    text = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
    text = _KV_RE.sub(lambda m: m.group(1) + REDACTED, text)
    return text


def sanitize_for_storage(value: Any) -> Any:
    """Return ``value`` with credential-bearing content redacted, recursively.

    Handles ``str``, ``dict`` (redacts secret-keyed values), and ``list``/``tuple``
    (element-wise); other scalars pass through unchanged.
    """
    if isinstance(value, str):
        return _sanitize_str(value)
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _SECRET_KEY_RE.search(key):
                result[key] = REDACTED
            else:
                result[key] = sanitize_for_storage(item)
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize_for_storage(item) for item in value]
    return value
