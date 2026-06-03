"""Source-registry I/O: load git-tracked YAML and mirror it into the ``source`` table.

YAML parsing/validation lives in ``atlascore.schemas.source`` (pure); this module adds
file discovery and the idempotent DB upsert (keyed on the unique ``name``). No secrets
ever reach a column — ``credential_ref`` is an env-key name (CLAUDE.md §3, §14).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlascore.core.errors import SourceValidationError
from atlascore.schemas.source import SourceRecord, SourceYAML
from atlascore.storage.postgres.models import Source

# The git-tracked source registry (source of truth), relative to the repo root.
DEFAULT_REGISTRY_DIR = Path("sources")


def load_source_yaml(path: Path) -> SourceYAML:
    """Read, parse, and validate one source YAML file.

    Enforces that the file stem matches the source ``id`` slug (filename convention).
    Raises :class:`SourceValidationError` for an unreadable file, malformed YAML, a
    non-mapping document, or a stem mismatch; Pydantic ``ValidationError`` for a
    schema / duration-grammar / default-deny-policy violation.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SourceValidationError(f"{path}: cannot read file: {exc}") from exc
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SourceValidationError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SourceValidationError(
            f"{path}: source YAML must be a mapping, got {type(raw).__name__}"
        )
    source = SourceYAML.model_validate(raw)
    if source.id != path.stem:
        raise SourceValidationError(
            f"{path}: source id {source.id!r} must match the filename stem {path.stem!r}"
        )
    return source


def discover_source_files(root: Path = DEFAULT_REGISTRY_DIR) -> list[Path]:
    """Return all ``*.yaml`` files under the registry directory, sorted (deterministic)."""
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.yaml") if p.is_file())


def _record_to_columns(record: SourceRecord) -> dict[str, Any]:
    """Map a validated record onto ``source`` column values (jsonb sub-models -> dict)."""
    return {
        "name": record.name,
        "category": record.category,
        "access_method": record.access_method.value,
        "base_url": record.base_url,
        "policy_status": record.policy_status.value,
        "rate_limit": record.rate_limit.model_dump() if record.rate_limit else {},
        "retention": record.retention.model_dump(exclude_none=True) if record.retention else {},
        "freshness": record.freshness.model_dump() if record.freshness else {},
        "config": record.config.model_dump(exclude_none=True),
        "parser_ref": record.parser_ref,
        "credential_ref": record.credential_ref,
        "enabled": record.enabled,
        "schema_version": record.schema_version,
    }


def upsert_source(session: Session, record: SourceRecord) -> Source:
    """Insert or update a ``source`` row, keyed on the stable slug (idempotent; ADR 0013).

    The slug (``config.slug``) is the stable logical identity, so re-syncing after a
    ``name`` change updates the same row instead of orphaning it. Falls back to matching
    on ``name`` for legacy rows without a slug. A new row gets a fresh UUIDv7 PK (the ORM
    default); an existing row keeps its id. The caller controls the transaction.
    """
    columns = _record_to_columns(record)
    existing: Source | None = None
    slug = record.config.slug
    if slug is not None:
        existing = session.scalar(select(Source).where(Source.config["slug"].astext == slug))
    if existing is None:
        existing = session.scalar(select(Source).where(Source.name == record.name))
    if existing is None:
        row = Source(**columns)
        session.add(row)
        return row
    for key, value in columns.items():
        setattr(existing, key, value)
    return existing


def get_source(session: Session, key: str) -> Source | None:
    """Look up a source by its YAML ``id`` slug (``config->>'slug'``), then by ``name``.

    Slug-first: slugs (``[a-z0-9_]``) and human-readable names rarely collide, but if one
    source's ``name`` equalled another's slug, the slug match wins (intentional, documented).
    """
    by_slug = session.scalar(select(Source).where(Source.config["slug"].astext == key))
    if by_slug is not None:
        return by_slug
    return session.scalar(select(Source).where(Source.name == key))


def list_sources(session: Session) -> list[Source]:
    """Return all registered sources, ordered by name."""
    return list(session.scalars(select(Source).order_by(Source.name)))
