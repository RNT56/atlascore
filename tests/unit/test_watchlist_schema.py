"""Unit tests for the B18 alert watchlist schema + loader (offline; no DB)."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlascore.core.errors import ConfigError
from atlascore.schemas.watchlist import Watchlist
from atlascore.storage.watchlist import load_watchlist

pytestmark = pytest.mark.unit


def test_parse_round_trip_and_cik_normalization() -> None:
    wl = Watchlist.model_validate(
        {
            "enabled": True,
            "cve": {"min_cvss": 9.0, "severities": ["critical"]},
            "sec": {"ciks": ["0000320193", "320193"], "forms": ["10-k"]},
        }
    )
    assert wl.cve.min_cvss == 9.0
    assert wl.cve.severities == ["CRITICAL"]  # upper-normalized
    assert wl.sec.ciks == ["320193", "320193"]  # zero-stripped
    assert wl.sec.forms == ["10-K"]
    assert wl.sec.cik_matches("0000320193") and wl.sec.cik_matches("320193")
    assert not wl.sec.cik_matches("999")


def test_is_active_and_empty() -> None:
    assert Watchlist.empty().is_active is False  # no criteria → inactive (fire on everything)
    assert Watchlist.model_validate({"cve": {"min_cvss": 7.0}}).is_active is True
    assert Watchlist.model_validate({"sec": {"ciks": ["1"]}}).is_active is True
    assert Watchlist.model_validate({"enabled": False, "cve": {"min_cvss": 7.0}}).is_active is False


def test_extra_forbidden_and_cvss_bounds() -> None:
    with pytest.raises(ValueError, match="extra"):
        Watchlist.model_validate({"bogus": 1})
    with pytest.raises(ValueError):
        Watchlist.model_validate({"cve": {"min_cvss": 11}})


def test_load_watchlist_absent_returns_empty(tmp_path: Path) -> None:
    assert load_watchlist(tmp_path / "nope.yaml").is_active is False


def test_load_watchlist_parses_file(tmp_path: Path) -> None:
    p = tmp_path / "wl.yaml"
    p.write_text("cve:\n  min_cvss: 9.0\nsec:\n  ciks: ['320193']\n")
    wl = load_watchlist(p)
    assert wl.is_active and wl.cve.min_cvss == 9.0 and wl.sec.ciks == ["320193"]


def test_load_watchlist_malformed_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("cve:\n  min_cvss: 99\n")  # out of bounds → validation error
    with pytest.raises(ConfigError, match="invalid watchlist"):
        load_watchlist(p)


def test_committed_watchlist_is_valid() -> None:
    # The repo-root watchlist.yaml must always parse (it is governed config).
    wl = load_watchlist(Path("watchlist.yaml"))
    assert wl.is_active
