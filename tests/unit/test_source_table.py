"""Unit tests that lock the ``source`` ORM table shape + naming-convention names.

These assert the SQLAlchemy model's metadata directly (no DB), so a future change to
columns, constraint/index names, or server defaults that would diverge from migration
0001 / docs/schemas §2.1 fails fast. Guards the naming-convention token expansion
(``ck_source_*``) so nobody "fixes" the migration into double-prefixed names.
"""

from __future__ import annotations

import pytest

from atlascore.storage.postgres.models import Source

pytestmark = pytest.mark.unit

EXPECTED_COLUMNS = {
    "id",
    "name",
    "category",
    "access_method",
    "base_url",
    "policy_status",
    "rate_limit",
    "retention",
    "freshness",
    "config",
    "checkpoint",  # B20: runtime-owned incremental crawl cursor (migration 0011)
    "parser_ref",
    "credential_ref",
    "enabled",
    "schema_version",
    "created_at",
    "updated_at",
}

# Convention-expanded names (Base.metadata naming_convention); migration 0001 must match.
EXPECTED_NAMED_OBJECTS = {
    "pk_source",
    "uq_source_name",
    "ck_source_access_method_allowed",
    "ck_source_policy_status_allowed",
    "ix_source_category_enabled",
    "ix_source_policy_status",
}


def test_columns_match_spec() -> None:
    assert {c.name for c in Source.__table__.columns} == EXPECTED_COLUMNS


def test_constraint_and_index_names_use_convention() -> None:
    names: set[str] = set()
    for con in Source.__table__.constraints:
        # SQLAlchemy uses a non-str sentinel for unnamed constraints; keep real names only.
        if isinstance(con.name, str):
            names.add(con.name)
    for idx in Source.__table__.indexes:
        if isinstance(idx.name, str):
            names.add(idx.name)
    assert names >= EXPECTED_NAMED_OBJECTS, f"missing: {EXPECTED_NAMED_OBJECTS - names}"
    assert Source.__table__.primary_key.name == "pk_source"


def test_default_deny_server_defaults() -> None:
    cols = Source.__table__.c
    # policy_status defaults to 'unknown'; enabled to false; jsonb to '{}'; timestamps to now().
    assert cols.policy_status.server_default is not None
    assert cols.enabled.server_default is not None
    for jsonb_col in ("rate_limit", "retention", "freshness", "config"):
        assert cols[jsonb_col].server_default is not None
    assert cols.created_at.server_default is not None
    assert cols.updated_at.server_default is not None
    # schema_version has NO server_default (spec DDL §2.1); id is app-generated (uuid7).
    assert cols.schema_version.server_default is None
    assert cols.id.server_default is None


def test_nullability_matches_spec() -> None:
    cols = Source.__table__.c
    # base_url, parser_ref, credential_ref are nullable; the rest NOT NULL.
    assert cols.base_url.nullable is True
    assert cols.parser_ref.nullable is True
    assert cols.credential_ref.nullable is True
    for required in (
        "name",
        "category",
        "access_method",
        "policy_status",
        "enabled",
        "schema_version",
    ):
        assert cols[required].nullable is False
