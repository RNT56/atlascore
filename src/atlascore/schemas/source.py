"""The ``source`` registry record (canonical row shape) + policy/access enums.

Authoritative spec: docs/schemas/README.md §2.1 and docs/connectors/README.md §4.
This is the in-memory mirror of a ``source`` table row. The git-tracked YAML is
the source of truth and round-trips losslessly into this shape; the YAML loader,
duration-grammar validation, and default-deny enforcement land in B2. B1 fixes the
shape, the enums, and the default-deny predicate.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AccessMethod(StrEnum):
    """How a source is fetched. ``html``/``browser`` are rare approved exceptions
    (none in MVP)."""

    API = "api"
    RSS = "rss"
    HTML = "html"
    BROWSER = "browser"


class PolicyStatus(StrEnum):
    """Default-deny policy states (default ``unknown`` => disabled)."""

    APPROVED = "approved"
    RESTRICTED = "restricted"
    PENDING = "pending"
    UNKNOWN = "unknown"
    PROHIBITED = "prohibited"

    @property
    def is_fetchable(self) -> bool:
        """Whether this policy state permits fetching *in principle*.

        ``approved`` is unconditionally fetchable; ``restricted`` is fetchable only
        once its encoded constraints are satisfied — that per-source constraint check
        is added in B2. ``pending``/``unknown``/``prohibited`` are always denied.
        """
        return self in (PolicyStatus.APPROVED, PolicyStatus.RESTRICTED)


class RateLimit(BaseModel):
    """Per-host token-bucket configuration (``{max_requests, window_seconds}``)."""

    model_config = ConfigDict(extra="forbid")

    max_requests: int = Field(gt=0)
    window_seconds: int = Field(gt=0)


class Retention(BaseModel):
    """Per-data-class retention (duration grammar validated in B2)."""

    model_config = ConfigDict(extra="forbid")

    raw: str
    normalized: str
    audit: str | None = None


class Freshness(BaseModel):
    """Freshness thresholds; ``freshness_lag`` past ``warn_after``/``error_after``
    drives source_health."""

    model_config = ConfigDict(extra="forbid")

    target: str
    warn_after: str
    error_after: str


class SourceConfig(BaseModel):
    """The ``source.config`` jsonb round-trip home (docs/schemas R9).

    Holds the YAML fields with no dedicated column: ``policy.allowed_uses``,
    ``policy.robots``, ``fetch.connector``, ``fetch.schedule`` (cron the scheduler
    reads), and ``fetch.timeout_seconds``.
    """

    model_config = ConfigDict(extra="forbid")

    allowed_uses: list[str] = Field(default_factory=list)
    robots: str | None = None
    connector: str | None = None
    schedule: str | None = None
    timeout_seconds: int | None = None


class SourceRecord(BaseModel):
    """A ``source`` table row. Default-deny: ``policy_status`` defaults to ``unknown``
    and ``enabled`` to ``False``."""

    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    name: str
    category: str
    access_method: AccessMethod
    base_url: str | None = None
    policy_status: PolicyStatus = PolicyStatus.UNKNOWN
    rate_limit: RateLimit | None = None
    retention: Retention | None = None
    freshness: Freshness | None = None
    config: SourceConfig = Field(default_factory=SourceConfig)
    parser_ref: str | None = None
    credential_ref: str | None = None
    enabled: bool = False
    schema_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_fetchable(self) -> bool:
        """A source is fetched only when enabled AND its policy permits it."""
        return self.enabled and self.policy_status.is_fetchable
