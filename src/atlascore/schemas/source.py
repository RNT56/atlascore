"""The ``source`` registry: the canonical YAML shape, the table-row record, and enums.

Authoritative spec: docs/schemas/README.md §2.1 and docs/connectors/README.md §4.
The git-tracked YAML (``SourceYAML``) is the source of truth and round-trips losslessly
into the table-row shape (``SourceRecord``) via ``SourceYAML.to_record()`` (R9). Duration
grammar (retention/freshness) and the default-deny policy gate are enforced here.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from atlascore.core.durations import parse_duration


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
        once its encoded constraints are satisfied — that per-source constraint check is
        enforced by the connector in a later block (not B2). ``pending``/``unknown``/
        ``prohibited`` are always denied.
        """
        return self in (PolicyStatus.APPROVED, PolicyStatus.RESTRICTED)


class RateLimit(BaseModel):
    """Per-host token-bucket configuration (``{max_requests, window_seconds}``)."""

    model_config = ConfigDict(extra="forbid")

    max_requests: int = Field(gt=0)
    window_seconds: int = Field(gt=0)


class Retention(BaseModel):
    """Per-data-class retention. Durations use ``Nd|Nw|Nm|Ny`` or ``permanent`` (no hours)."""

    model_config = ConfigDict(extra="forbid")

    raw: str
    normalized: str
    audit: str | None = None

    @field_validator("raw", "normalized")
    @classmethod
    def _validate_retention(cls, v: str) -> str:
        parse_duration(v, allow_permanent=True, allow_hours=False)
        return v

    @field_validator("audit")
    @classmethod
    def _validate_audit(cls, v: str | None) -> str | None:
        if v is not None:
            parse_duration(v, allow_permanent=True, allow_hours=False)
        return v


class Freshness(BaseModel):
    """Freshness thresholds; ``freshness_lag`` past ``warn_after``/``error_after`` drives
    source_health. Durations use ``Nh|Nd|Nw|Nm|Ny`` (finite; ``permanent`` is rejected)."""

    model_config = ConfigDict(extra="forbid")

    target: str
    warn_after: str
    error_after: str

    @field_validator("target", "warn_after", "error_after")
    @classmethod
    def _validate_freshness(cls, v: str) -> str:
        parse_duration(v, allow_permanent=False, allow_hours=True)
        return v


class SourceConfig(BaseModel):
    """The ``source.config`` jsonb round-trip home (docs/schemas R9).

    Holds the YAML fields with no dedicated column: ``policy.allowed_uses``,
    ``policy.robots``, ``fetch.connector``, ``fetch.schedule`` (cron the scheduler
    reads), and ``fetch.timeout_seconds``.
    """

    model_config = ConfigDict(extra="forbid")

    # The YAML's logical `id` slug (stable, used for dedupe_keys + filename). The table
    # PK is a UUIDv7; the slug has no dedicated column, so it round-trips here.
    slug: str | None = None
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


# --- Source-registry YAML (the git-tracked source of truth, docs/connectors §4) ---

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
_CONNECTOR_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


class SourcePolicy(BaseModel):
    """The YAML ``policy`` block (status + robots advisory + allowed uses)."""

    model_config = ConfigDict(extra="forbid")

    status: PolicyStatus = PolicyStatus.UNKNOWN
    robots: str | None = None
    allowed_uses: list[str] = Field(default_factory=list)


class SourceCredentials(BaseModel):
    """``credentials.ref`` names an ENV KEY resolved at runtime — NEVER a secret value."""

    model_config = ConfigDict(extra="forbid")

    ref: str | None = None


class SourceFetch(BaseModel):
    """The YAML ``fetch`` block: the connector, per-source cron, and request timeout."""

    model_config = ConfigDict(extra="forbid")

    connector: str
    schedule: str
    timeout_seconds: int = Field(gt=0)

    @field_validator("connector")
    @classmethod
    def _validate_connector(cls, v: str) -> str:
        if _CONNECTOR_RE.match(v) is None:
            raise ValueError("fetch.connector must be 'module.path:ClassName'")
        return v

    @field_validator("schedule")
    @classmethod
    def _validate_schedule(cls, v: str) -> str:
        if len(v.split()) != 5:
            raise ValueError("fetch.schedule must be a 5-field cron expression")
        return v


class SourceYAML(BaseModel):
    """A canonical source-registry YAML file. Validated by ``atlas source validate`` and
    mirrored into the ``source`` table via :meth:`to_record`. Contains NO secrets and NO
    private endpoints (CLAUDE.md §3, §16)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    category: str
    access_method: AccessMethod
    base_url: str
    enabled: bool = False
    policy: SourcePolicy = Field(default_factory=SourcePolicy)
    rate_limit: RateLimit
    freshness: Freshness
    retention: Retention
    credentials: SourceCredentials = Field(default_factory=SourceCredentials)
    fetch: SourceFetch

    @field_validator("id")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if _SLUG_RE.match(v) is None:
            raise ValueError("id must be a lowercase 'provider_dataset' slug ([a-z0-9_])")
        return v

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str) -> str:
        # B2 validates the scheme only (http for local fixtures, https otherwise).
        # Full scheme/SSRF/IP enforcement is the B4 fetcher's URL-safety guard, applied
        # at fetch time to the resolved IP (docs/connectors §5.4 / §9).
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url must be an http(s) URL")
        return v

    @model_validator(mode="after")
    def _validate_policy_enablement(self) -> Self:
        # allowed_uses is mandatory for a fetchable policy (approved/restricted).
        if self.policy.status.is_fetchable and not self.policy.allowed_uses:
            raise ValueError(
                f"policy.allowed_uses is required when status is '{self.policy.status.value}'"
            )
        # Default-deny guardrail: refuse a config that enables a non-fetchable policy.
        if self.enabled and not self.policy.status.is_fetchable:
            raise ValueError(
                f"cannot enable a source with policy '{self.policy.status.value}' "
                "(default-deny): set policy.status to approved/restricted, or enabled: false"
            )
        return self

    def to_record(self) -> SourceRecord:
        """Map this YAML onto a ``source`` table row (lossless round-trip, R9)."""
        return SourceRecord(
            id=None,
            name=self.name,
            category=self.category,
            access_method=self.access_method,
            base_url=self.base_url,
            policy_status=self.policy.status,
            rate_limit=self.rate_limit,
            retention=self.retention,
            freshness=self.freshness,
            config=SourceConfig(
                slug=self.id,
                allowed_uses=self.policy.allowed_uses,
                robots=self.policy.robots,
                connector=self.fetch.connector,
                schedule=self.fetch.schedule,
                timeout_seconds=self.fetch.timeout_seconds,
            ),
            parser_ref=None,
            credential_ref=self.credentials.ref,
            enabled=self.enabled,
            schema_version=1,
        )
