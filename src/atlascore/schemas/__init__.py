"""Pydantic v2 boundary models — the canonical shape the storage layer mirrors.

The data model is specified authoritatively in docs/schemas/README.md (the 12
tables). B1 ships only the ``source`` shape; later blocks add the rest.
"""

from __future__ import annotations

from atlascore.schemas.source import (
    AccessMethod,
    Freshness,
    PolicyStatus,
    RateLimit,
    Retention,
    SourceConfig,
    SourceCredentials,
    SourceFetch,
    SourcePolicy,
    SourceRecord,
    SourceYAML,
)

__all__ = [
    "AccessMethod",
    "Freshness",
    "PolicyStatus",
    "RateLimit",
    "Retention",
    "SourceConfig",
    "SourceCredentials",
    "SourceFetch",
    "SourcePolicy",
    "SourceRecord",
    "SourceYAML",
]

# Bump when any persisted record's shape changes; written onto rows as schema_version.
SCHEMA_VERSION = 1
