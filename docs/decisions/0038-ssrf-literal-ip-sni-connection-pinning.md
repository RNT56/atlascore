# 0038. Pin outbound fetches to validated literal IPs with SNI preservation

- **Status:** Accepted
- **Date:** 2026-07-12

## Context

The URL guard validated every DNS answer but `httpx` performed a second hostname lookup when it
opened the socket. ADR 0014 accepted that DNS-rebinding window temporarily, while the public security
contract already required resolve-then-pin.

## Decision

`SafeFetcher` rewrites each request URL to a validated literal IP, sets the original authority in
`Host`, and supplies the original HTTPS hostname through HTTPX's public `sni_hostname` extension.
Validated addresses are tried in resolver order on connection failure. Redirects resolve against the
public URL and repeat the full guard; custom credential headers survive only same-origin redirects.
Production fetch clients set `trust_env=False` so an environment proxy cannot replace the pinned path.

## Consequences

- The DNS-rebinding TOCTOU window in ADR 0014 is closed; ADR 0014 is superseded.
- TLS certificate and virtual-host routing still use the intended hostname.
- Dedicated regressions assert the literal IP, `Host`, SNI, redirect, and failover behavior.
