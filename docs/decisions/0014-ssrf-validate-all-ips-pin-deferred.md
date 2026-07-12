# 0014. SSRF guard validates all resolved IPs + re-validates every redirect hop; connection-pinning deferred

> **Superseded by [ADR 0038](0038-ssrf-literal-ip-sni-connection-pinning.md) on 2026-07-12.**

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Operator (single-operator project)
- **Spec reference:** AtlasCore Canonical Decisions Spec v1 (2026-06-03), §14 (SSRF/URL-safety), docs/connectors §9; MVP block **B4**.

## Context

CLAUDE.md §14 / docs/connectors §9 require the fetcher (and any agent-supplied URL) to: allow only
`http`/`https`; **resolve the hostname and validate all resolved IPs before connect, then pin the
connection to the validated IP** (anti-DNS-rebinding); and **re-validate every redirect hop** with a
capped redirect count, rejecting loopback/RFC1918/CGNAT/link-local/ULA/IPv4-mapped/metadata.

Connection-pinning (forcing the socket to the exact validated IP while preserving TLS SNI/cert
validation) requires a custom httpcore transport. It cannot be exercised by the offline (respx) test
suite — respx replaces the transport — so a pinning bug (e.g. broken HTTPS cert validation) could
ship unverified, which is worse than a documented validation-only posture. B4 must ship a fetcher
that is safe and fully tested offline.

## Decision

B4 implements **SSRF validation** in `atlascore.security.url_safety`
(`is_blocked_ip`/`assert_url_allowed`) and the fetcher (`SafeFetcher`):

- scheme allowlist (`http`/`https`);
- resolve the host (injected resolver; `socket.getaddrinfo` by default) and reject if **any**
  resolved IP is non-public — loopback, RFC1918, CGNAT `100.64.0.0/10`, link-local, ULA, IPv4-mapped
  IPv6, multicast/reserved/unspecified, cloud metadata, or otherwise not globally routable
  (`not ip.is_global`, with explicit range checks + IPv4-mapped recursion as belt-and-suspenders);
- **re-validate every redirect hop** (`follow_redirects=False` + a manual loop that calls
  `assert_url_allowed` per hop) with a capped redirect count; reject redirects to disallowed schemes.

**Connection-pinning to the validated IP is DEFERRED** (documented hardening follow-up). The
validation runs immediately before each connect and on every redirect hop, so DNS rebinding would
require winning a sub-second TOCTOU race between our resolution and httpx's connect resolution.

The **single-fetch-worker startup guard (R16)** IS implemented in B4 (`worker.fetch_worker_lock`, a
Postgres `pg_try_advisory_lock`): a second fetch worker/`atlas crawl once` is refused while one holds
the lock, keeping the in-process per-host token buckets a correct enforcement of documented rate
limits.

## Consequences

- **Positive:** SSRF protection is comprehensive (27-case adversarial classification audit passes,
  incl. IPv4-mapped/6to4/NAT64/metadata edges) and fully unit-tested with an injected resolver;
  redirects are re-validated; the fetcher is verified live (a real Fed RSS fetch + a real 304
  conditional response) and offline (respx). The single-fetch-worker invariant is enforced.
- **Negative / costs:** the final anti-rebinding *pin* is not yet in place — a sub-second
  DNS-rebinding TOCTOU window remains. Acceptable for a single-operator box fetching a curated set of
  approved public sources; not acceptable if the fetcher is ever exposed to untrusted callers at scale.
- **Follow-ups / obligations:** add a pinning httpcore transport (resolve→validate→connect to the
  validated IP with preserved SNI/cert host) with a live HTTPS verification, before any multi-user or
  internet-exposed deployment. Until then, the validation + per-redirect re-check is the guarantee.
- **Revisit triggers:** the MCP server (or anything) starts accepting agent-supplied URLs to fetch;
  the box stops being single-operator; or results are published (review the whole threat model).
