"""Secrets handling: credential resolution + the `sanitize_for_storage` write-time redactor.

Credentials resolve from the environment (a source's `credential_ref` names an env key,
never a value). `sanitize_for_storage` is the single helper every writer of
`crawl_job.last_error`/`payload` and `audit_log.detail` must call (CLAUDE.md §14).
"""

from __future__ import annotations

from atlascore.security.secrets.credentials import resolve_credential
from atlascore.security.secrets.sanitize import REDACTED, sanitize_for_storage

__all__ = ["REDACTED", "resolve_credential", "sanitize_for_storage"]
