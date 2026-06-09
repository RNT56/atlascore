"""Unit tests for the read-only WebUI (security-critical helpers + no-DB render; `web` extra)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from fastapi.testclient import TestClient

from atlascore.api.app import create_app
from atlascore.api.web import _highlight, _lag, _safe_url, _sparkline
from atlascore.schemas.document import DocumentRecord

pytestmark = pytest.mark.unit


def test_safe_url_allows_http_rejects_scheme_tricks() -> None:
    assert _safe_url("https://x.test/a?b=1") == "https://x.test/a?b=1"
    assert _safe_url("http://x.test") == "http://x.test"
    assert _safe_url("javascript:alert(1)") is None
    assert _safe_url("data:text/html,<script>alert(1)</script>") is None
    assert _safe_url("  JaVaScRiPt:alert(1)") is None  # case/space-insensitive
    assert _safe_url(None) is None
    assert _safe_url("") is None


def test_document_record_coerces_non_http_canonical_url() -> None:
    base = {
        "source_id": uuid4(),
        "raw_document_id": uuid4(),
        "content_hash": "h",
        "parser_version": "p",
    }
    assert DocumentRecord(canonical_url="https://x.test", **base).canonical_url == "https://x.test"
    assert DocumentRecord(canonical_url="javascript:alert(1)", **base).canonical_url is None
    assert DocumentRecord(canonical_url=None, **base).canonical_url is None


def test_highlight_escapes_untrusted_then_marks() -> None:
    # The match markers «…» become <mark>; everything else is HTML-escaped FIRST (no raw HTML survives).
    out = str(_highlight("«Apple» <b>x</b> & <script>alert(1)</script>"))
    assert "<mark>Apple</mark>" in out
    assert "<b>" not in out and "&lt;b&gt;" in out
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "&amp;" in out


def test_highlight_empty() -> None:
    assert str(_highlight(None)) == ""
    assert str(_highlight("")) == ""


def test_sparkline_is_numeric_only_svg() -> None:
    pts = [
        SimpleNamespace(value=Decimal("1.0")),
        SimpleNamespace(value=Decimal("2.5")),
        SimpleNamespace(value=None),
    ]
    svg = str(_sparkline(pts))
    assert svg.startswith("<svg") and "<polyline" in svg
    assert "<script" not in svg  # purely numeric coordinates, no injection surface


def test_sparkline_needs_two_points() -> None:
    assert str(_sparkline([SimpleNamespace(value=Decimal("1"))])) == ""
    assert str(_sparkline([])) == ""


def test_lag_filter() -> None:
    assert _lag(None) == "—"
    assert _lag(timedelta(days=3, hours=22)) == "3d22h"
    assert _lag(timedelta(hours=2, minutes=5)) == "2h5m"
    assert _lag(timedelta(minutes=9)) == "9m"


def test_search_renders_without_query_or_db() -> None:
    # No `q` => no DB query; the page (and base layout) still renders.
    r = TestClient(create_app()).get("/search")
    assert r.status_code == 200
    assert "Search documents" in r.text
    assert "read-only" in r.text  # base layout rendered


def test_ui_routes_registered() -> None:
    paths = {r.path for r in create_app().routes}
    assert {
        "/",
        "/search",
        "/documents/{document_id}",
        "/alerts",
        "/sources",
        "/macro",
        "/macro/series",
    } <= paths
