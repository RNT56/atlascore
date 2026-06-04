"""SSRF / URL-safety guard for the fetcher and any agent-supplied URL."""

from __future__ import annotations

from atlascore.security.url_safety.guard import (
    ALLOWED_SCHEMES,
    Resolver,
    ValidatedTarget,
    assert_url_allowed,
    is_blocked_ip,
    parse_target,
)

__all__ = [
    "ALLOWED_SCHEMES",
    "Resolver",
    "ValidatedTarget",
    "assert_url_allowed",
    "is_blocked_ip",
    "parse_target",
]
