"""SSRF / URL-safety guard (CLAUDE.md §14, docs/connectors §9).

Binding for the fetcher AND any agent-supplied URL:

- scheme allowlist: ``http``/``https`` only;
- resolve the hostname and validate **all** resolved IPs before connect — reject loopback,
  RFC1918 private, CGNAT ``100.64.0.0/10``, link-local ``169.254.0.0/16`` + ``fe80::/10``,
  ULA ``fc00::/7``, IPv4-mapped IPv6, multicast/reserved/unspecified, and the cloud-metadata
  endpoints ``169.254.169.254`` / ``fd00:ec2::254`` (anything not globally routable);
- the caller pins the connection to a validated IP (anti-DNS-rebinding) and re-runs
  :func:`assert_url_allowed` on **every redirect hop** with a capped redirect count.

The IP classification is pure and fully unit-tested; DNS resolution is injected so tests run
with no live network.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from atlascore.core.errors import SsrfError

ALLOWED_SCHEMES = frozenset({"http", "https"})
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_METADATA_V6 = ipaddress.ip_network("fd00:ec2::/32")  # AWS IMDS IPv6 (fd00:ec2::254)

# A DNS resolver returns the resolved IP strings for a host. Injected for tests.
Resolver = Callable[[str], list[str]]

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class ValidatedTarget:
    """A URL that passed the guard, plus the validated IPs it resolved to."""

    scheme: str
    host: str
    port: int
    ips: tuple[str, ...]


def _default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [str(info[4][0]) for info in infos]


def is_blocked_ip(ip: _IpAddress | str) -> bool:
    """True if ``ip`` is NOT a safe, globally-routable public address.

    Errs safe: anything not clearly public (loopback/private/link-local/CGNAT/ULA/
    IPv4-mapped/metadata/reserved/multicast/unspecified, or otherwise non-global) is blocked.
    """
    addr: _IpAddress = ipaddress.ip_address(ip) if isinstance(ip, str) else ip
    # IPv4-mapped IPv6 (::ffff:a.b.c.d) — validate the embedded IPv4.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return is_blocked_ip(addr.ipv4_mapped)
    if (
        addr.is_loopback
        or addr.is_link_local  # 169.254.0.0/16 + fe80::/10 (covers IMDS 169.254.169.254)
        or addr.is_private  # RFC1918 + ULA fc00::/7 + others
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return True
    if isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr in _METADATA_V6:
        return True
    # Final catch-all: only globally-routable public addresses are allowed.
    return not addr.is_global


def parse_target(url: str) -> tuple[str, str, int]:
    """Validate the scheme and extract ``(scheme, host, port)``. Raises :class:`SsrfError`."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise SsrfError(f"scheme {scheme!r} not allowed (only http/https)")
    host = parts.hostname
    if not host:
        raise SsrfError(f"URL has no host: {url!r}")
    port = parts.port or (443 if scheme == "https" else 80)
    return scheme, host, port


def assert_url_allowed(url: str, *, resolver: Resolver | None = None) -> ValidatedTarget:
    """Validate scheme + resolve the host + reject if ANY resolved IP is disallowed.

    Returns the :class:`ValidatedTarget` (with validated IPs to pin to). Raises
    :class:`SsrfError` on a bad scheme, an unresolvable host, or any disallowed IP.
    """
    scheme, host, port = parse_target(url)
    # A literal IP host is validated directly (no DNS).
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if is_blocked_ip(literal):
            raise SsrfError(f"host IP {host} is not allowed")
        return ValidatedTarget(scheme, host, port, (str(literal),))

    resolve = resolver or _default_resolver
    try:
        resolved = resolve(host)
    except OSError as exc:
        raise SsrfError(f"cannot resolve host {host!r}: {exc}") from exc
    if not resolved:
        raise SsrfError(f"host {host!r} resolved to no addresses")
    for ip in resolved:
        if is_blocked_ip(ip):
            raise SsrfError(f"host {host!r} resolved to a disallowed address ({ip})")
    return ValidatedTarget(scheme, host, port, tuple(resolved))
