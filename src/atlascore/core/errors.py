"""Base error hierarchy. Service boundaries add context; errors are never swallowed."""

from __future__ import annotations


class AtlasError(Exception):
    """Root of all AtlasCore-raised errors."""


class ConfigError(AtlasError):
    """Invalid or missing configuration (settings, environment, source YAML)."""


class PolicyError(AtlasError):
    """A default-deny policy violation: an attempt to act on a non-fetchable source."""


class SourceValidationError(ConfigError):
    """A source-registry YAML failed validation (schema, grammar, or policy)."""


class SecurityError(AtlasError):
    """A security guard refused an operation."""


class SsrfError(SecurityError):
    """A URL/host failed the SSRF / URL-safety guard (bad scheme or disallowed IP)."""


class FetchError(AtlasError):
    """A fetch failed validation (bad status, MIME, size) or exceeded the redirect cap."""


class FetchWorkerBusyError(AtlasError):
    """Another fetch worker already holds the single-fetch-worker lock (R16)."""


class AlertTransitionError(AtlasError):
    """An illegal alert lifecycle transition (e.g. resolving a purged/terminal alert)."""


class SchedulerBusyError(AtlasError):
    """Another scheduler instance already holds the single-scheduler lock (§17)."""
