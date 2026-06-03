"""Base error hierarchy. Service boundaries add context; errors are never swallowed."""

from __future__ import annotations


class AtlasError(Exception):
    """Root of all AtlasCore-raised errors."""


class ConfigError(AtlasError):
    """Invalid or missing configuration (settings, environment, source YAML)."""


class PolicyError(AtlasError):
    """A default-deny policy violation: an attempt to act on a non-fetchable source."""
