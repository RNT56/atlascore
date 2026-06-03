"""Security regression: `sanitize_for_storage` must never let a secret reach a column.

Covers the real connector cases: FRED `?api_key=`, NVD `apiKey` header, GitHub
`Authorization`/bearer, URL userinfo, key=value pairs, and secret-keyed dict values —
without over-redacting innocent fields (CLAUDE.md §14, docs/security).
"""

from __future__ import annotations

import pytest

from atlascore.security.secrets import REDACTED, sanitize_for_storage

pytestmark = pytest.mark.unit

# A real secret value that must NEVER survive sanitization, used across cases.
SECRET = "s3cr3t-VALUE-do-not-leak"


def test_strips_url_query_with_api_key() -> None:
    url = f"https://api.stlouisfed.org/fred/series?series_id=GDP&api_key={SECRET}"
    out = sanitize_for_storage(url)
    assert SECRET not in out
    assert "api_key" not in out
    assert out == "https://api.stlouisfed.org/fred/series"  # query dropped entirely


def test_strips_url_userinfo() -> None:
    out = sanitize_for_storage(f"https://user:{SECRET}@example.com/path?x=1")
    assert SECRET not in out
    assert out == "https://example.com/path"


def test_leaves_clean_url_untouched() -> None:
    assert sanitize_for_storage("https://example.com/feed.xml") == "https://example.com/feed.xml"


def test_redacts_apikey_header_kv() -> None:
    out = sanitize_for_storage(f"apiKey: {SECRET}")
    assert SECRET not in out
    assert REDACTED in out


def test_redacts_authorization_header_line() -> None:
    out = sanitize_for_storage(f"Authorization: token {SECRET}")
    assert SECRET not in out
    assert out.startswith("Authorization:")


def test_redacts_bearer_token() -> None:
    out = sanitize_for_storage(f"sent header Bearer {SECRET}")
    assert SECRET not in out
    assert "Bearer [REDACTED]" in out


def test_redacts_key_value_pairs() -> None:
    assert SECRET not in sanitize_for_storage(f"fred_api_key={SECRET}")
    assert SECRET not in sanitize_for_storage(f"client_secret={SECRET}")
    assert SECRET not in sanitize_for_storage(f"password: {SECRET}")


@pytest.mark.parametrize(
    "text",
    [
        # JSON response body, Python dict repr, httpx.Headers repr — the dominant real-world forms.
        '{"api_key":"%s"}',
        "{'headers': {'apiKey': '%s'}}",
        "httpx.Headers({'x-api-key': '%s', 'accept': 'json'})",
        'config={"client_secret": "%s"}',
        'token="%s"',
        "api_key='%s'",
        'password: "%s"',
    ],
)
def test_redacts_quoted_credential_values(text: str) -> None:
    # Regression for the quoted-value leak: a closing/opening quote must not let a secret through.
    out = sanitize_for_storage(text % SECRET)
    assert SECRET not in out
    assert REDACTED in out


def test_redacts_secret_keyed_dict_values_recursively() -> None:
    payload = {
        "url": f"https://x.test/?api_key={SECRET}",
        "api_key": SECRET,
        "Authorization": f"Bearer {SECRET}",
        "name": "innocent",
        "nested": {"password": SECRET, "count": 3},
        "headers": [f"X-Api-Key: {SECRET}"],
    }
    out = sanitize_for_storage(payload)
    assert out["api_key"] == REDACTED
    assert out["Authorization"] == REDACTED
    assert out["name"] == "innocent"  # not over-redacted
    assert out["nested"]["password"] == REDACTED
    assert out["nested"]["count"] == 3
    assert SECRET not in str(out)  # nothing leaks anywhere in the structure


def test_does_not_over_redact_innocent_text() -> None:
    msg = "fetch failed for source 42: HTTP 503 from https://example.com/feed.xml"
    assert sanitize_for_storage(msg) == msg


def test_does_not_redact_author_field() -> None:
    # "author" contains "auth" but is not a credential — must be preserved.
    out = sanitize_for_storage({"author": "Jane Doe", "title": "Report"})
    assert out["author"] == "Jane Doe"


def test_passes_through_non_strings() -> None:
    assert sanitize_for_storage(None) is None
    assert sanitize_for_storage(42) == 42
    assert sanitize_for_storage([1, "ok", {"token": SECRET}]) == [1, "ok", {"token": REDACTED}]
