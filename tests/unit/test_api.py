"""Unit tests for the read-only dashboard API (no DB; needs the `web` extra)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")  # skipped unless the `web` extra is installed (CI runs --all-extras)

from fastapi.testclient import TestClient

from atlascore.api.app import API_MAX_LIMIT, create_app

pytestmark = pytest.mark.unit


def _client() -> TestClient:
    return TestClient(create_app())  # engine created lazily; healthz/validation need no DB


def test_healthz_no_db() -> None:
    r = _client().get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_routes_are_read_only_surface() -> None:
    paths = {path for route in create_app().routes if (path := getattr(route, "path", None))}
    assert {"/healthz", "/v1/search", "/v1/alerts", "/v1/sources/health"} <= paths


def test_search_requires_query() -> None:
    # Missing `q` is rejected by FastAPI validation before the endpoint body (no DB hit).
    assert _client().get("/v1/search").status_code == 422


def test_search_limit_capped_by_validation() -> None:
    # limit above API_MAX_LIMIT is a 422 (validation), never a DB query.
    assert (
        _client().get("/v1/search", params={"q": "x", "limit": API_MAX_LIMIT + 1}).status_code
        == 422
    )
