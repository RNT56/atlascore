"""Unit tests for the source-YAML model, round-trip, and default-deny policy gate."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from atlascore.core.errors import SourceValidationError
from atlascore.schemas.source import PolicyStatus, SourceRecord
from atlascore.storage.sources import load_source_yaml

pytestmark = pytest.mark.unit

FIXTURES = Path("tests/fixtures/sources")


def test_loads_and_round_trips_to_record() -> None:
    record = load_source_yaml(FIXTURES / "example_rss.yaml").to_record()
    assert record.name == "Example RSS Source"
    assert record.policy_status is PolicyStatus.APPROVED
    assert record.credential_ref is None  # never a secret
    assert record.is_fetchable is True
    # R9 round-trip home: slug + policy/fetch fields live in config jsonb.
    assert record.config.slug == "example_rss"
    assert record.config.allowed_uses == ["internal_search"]
    assert record.config.connector == "atlascore.connectors.rss.feed:RssConnector"
    assert record.config.schedule == "*/30 * * * *"
    assert record.config.timeout_seconds == 30


def test_record_dump_reload_is_lossless() -> None:
    record = load_source_yaml(FIXTURES / "approved_rss.yaml").to_record()
    reloaded = SourceRecord.model_validate(record.model_dump(mode="json"))
    assert reloaded == record


def test_disabled_pending_is_valid_but_not_fetchable() -> None:
    record = load_source_yaml(FIXTURES / "disabled_pending.yaml").to_record()
    assert record.policy_status is PolicyStatus.PENDING
    assert record.enabled is False
    assert record.is_fetchable is False


@pytest.mark.parametrize(
    "fixture",
    ["pending_enabled", "unknown_enabled", "prohibited_enabled"],
)
def test_default_deny_refuses_enabled_non_approved(fixture: str) -> None:
    # Cannot enable a source whose policy is not approved/restricted.
    with pytest.raises(ValidationError, match="default-deny"):
        load_source_yaml(FIXTURES / f"{fixture}.yaml")


@pytest.mark.parametrize(
    ("fixture", "needle"),
    [
        ("bad_duration", "retention"),
        ("missing_allowed_uses", "allowed_uses"),
        ("bad_connector", "connector"),
        ("bad_cron", "cron"),
    ],
)
def test_schema_and_grammar_violations_rejected(fixture: str, needle: str) -> None:
    with pytest.raises(ValidationError, match=needle):
        load_source_yaml(FIXTURES / f"{fixture}.yaml")


def test_filename_stem_must_match_id(tmp_path: Path) -> None:
    bad = tmp_path / "wrong_name.yaml"
    bad.write_text((FIXTURES / "example_rss.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(SourceValidationError, match="filename stem"):
        load_source_yaml(bad)


def test_non_mapping_yaml_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "list_doc.yaml"
    bad.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(SourceValidationError, match="must be a mapping"):
        load_source_yaml(bad)


def test_missing_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(SourceValidationError, match="cannot read file"):
        load_source_yaml(tmp_path / "does_not_exist.yaml")


def test_malformed_yaml_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "broken.yaml"
    bad.write_text("name: [unclosed\n", encoding="utf-8")
    with pytest.raises(SourceValidationError, match="invalid YAML"):
        load_source_yaml(bad)
