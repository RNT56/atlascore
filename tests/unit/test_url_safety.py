"""Security: the SSRF / URL-safety guard. Pure logic with an injected resolver — no network."""

from __future__ import annotations

import pytest

from atlascore.core.errors import SsrfError
from atlascore.security.url_safety import assert_url_allowed, is_blocked_ip

pytestmark = [pytest.mark.unit, pytest.mark.security]

BLOCKED_IPS = [
    "127.0.0.1",  # loopback
    "10.0.0.5",  # RFC1918
    "192.168.1.1",  # RFC1918
    "172.16.0.1",  # RFC1918
    "169.254.169.254",  # cloud metadata (link-local)
    "169.254.0.1",  # link-local
    "100.64.0.1",  # CGNAT 100.64.0.0/10
    "0.0.0.0",  # unspecified
    "::1",  # IPv6 loopback
    "fe80::1",  # IPv6 link-local
    "fc00::1",  # IPv6 ULA
    "fd00:ec2::254",  # AWS IMDS IPv6
    "::ffff:10.0.0.1",  # IPv4-mapped private
]
ALLOWED_IPS = [
    "8.8.8.8",
    "1.1.1.1",
    "93.184.216.34",
    "2606:4700:4700::1111",
    "::ffff:8.8.8.8",  # IPv4-mapped public
]


@pytest.mark.parametrize("ip", BLOCKED_IPS)
def test_blocks_non_public_ips(ip: str) -> None:
    assert is_blocked_ip(ip) is True


@pytest.mark.parametrize("ip", ALLOWED_IPS)
def test_allows_public_ips(ip: str) -> None:
    assert is_blocked_ip(ip) is False


@pytest.mark.parametrize("url", ["ftp://host/x", "file:///etc/passwd", "gopher://h/", "ws://h/"])
def test_rejects_disallowed_schemes(url: str) -> None:
    with pytest.raises(SsrfError, match="scheme"):
        assert_url_allowed(url)


def test_allows_public_resolution() -> None:
    target = assert_url_allowed("https://good.example/path", resolver=lambda _h: ["8.8.8.8"])
    assert target.scheme == "https"
    assert target.host == "good.example"
    assert target.port == 443
    assert target.ips == ("8.8.8.8",)


def test_rejects_private_resolution() -> None:
    with pytest.raises(SsrfError, match="disallowed"):
        assert_url_allowed("https://evil.example/", resolver=lambda _h: ["10.0.0.1"])


def test_rejects_if_any_resolved_ip_is_disallowed() -> None:
    # A public + a private answer (DNS rebinding-style) must be refused.
    with pytest.raises(SsrfError, match="disallowed"):
        assert_url_allowed("https://x.example/", resolver=lambda _h: ["8.8.8.8", "10.0.0.1"])


def test_literal_ip_hosts_are_validated_without_dns() -> None:
    assert assert_url_allowed("https://8.8.8.8/").ips == ("8.8.8.8",)
    with pytest.raises(SsrfError):
        assert_url_allowed("http://127.0.0.1:8080/admin")
    with pytest.raises(SsrfError):
        assert_url_allowed("http://169.254.169.254/latest/meta-data/")


def test_unresolvable_host_is_refused() -> None:
    def boom(_host: str) -> list[str]:
        raise OSError("NXDOMAIN")

    with pytest.raises(SsrfError, match="cannot resolve"):
        assert_url_allowed("https://nope.example/", resolver=boom)


def test_host_resolving_to_nothing_is_refused() -> None:
    with pytest.raises(SsrfError, match="no addresses"):
        assert_url_allowed("https://empty.example/", resolver=lambda _h: [])


def test_missing_host_is_refused() -> None:
    with pytest.raises(SsrfError, match="no host"):
        assert_url_allowed("https:///nopath", resolver=lambda _h: ["8.8.8.8"])
