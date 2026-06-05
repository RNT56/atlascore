# 0033. B22 SEC full-filing extraction via sandboxed Tika (graceful fallback)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Operator (single-operator project)
- **Spec reference:** CLAUDE.md §8 (Apache Tika sandboxed, non-root, resource-limited), §14 (scan risky files before parsing; treat fetched content as hostile), §10 (connector lifecycle); post-MVP cycle B22. Builds on B10 (SEC metadata documents).

## Context

B10 turned each SEC filing into a **metadata-only** document (form/company/date + link). The actual
filing text (10-K/8-K PDFs and HTML) was never extracted. Filing bytes are hostile (malformed/poisoned
PDFs can exhaust memory/CPU or smuggle active content), so extraction must be sandboxed — and CI/the
sandbox have no Java, so the default path must stay green.

## Decision

- **Out-of-process, resource-bounded Tika** (`security.sandbox.tika`). `subprocess` mode runs
  `java -jar tika-app.jar --text` with a `preexec_fn` applying `RLIMIT_AS`/`RLIMIT_CPU` (POSIX), a
  wall-clock timeout, fixed argv (no shell), piped input, and an output cap — no network needed
  (file-in/text-out). `server` mode PUTs to a localhost `tika-server`. `none` (default) disables it.
  `subprocess`/`resource`/`httpx` are imported **lazily**, so the module imports with no Java and
  `tika_available()` is False.
- **`tika_available()` is the guard** (checked WITHOUT running Java/network: jar exists + `java` on
  PATH, or a server URL). `TikaClient.extract_text` raises `ConfigError` if called when unavailable;
  sandbox failures/timeouts raise `SecurityError`.
- **`FilingParser`** (`parsers.filing`, `filing@1.0.0`) wraps the Tika client (dependency-injected for
  tests), then strips control chars + collapses whitespace/blank-line runs — deterministic given a
  pinned Tika version. Returns a `ParsedFiling` (cleaned body + version + char count).
- **SEC connector opt-in + graceful fallback.** `SecSubmissionsConnector.extract_filing(fetcher, ctx,
  url)` returns the full text **only** when the source sets `fetch.params.extract_filings: 'true'`
  AND `tika_available()`; it fetches the primary document through the **same SSRF-guarded fetcher**
  (MIME allowlist `application/pdf`/`text/html`/…) and runs `FilingParser`. On opt-out, Tika-absent,
  or any fetch/extraction error it returns `None` and the connector keeps the B10 metadata body — a
  bad filing never fails the crawl, and CI/sandbox stay green without Java. **No schema change.**

## Consequences

- **Positive.** Real filing text becomes searchable when an operator installs Tika and opts a source
  in, with hostile input contained (separate process, address-space/CPU/time caps, non-root per the
  runbook, no network). The default deployment, CI, and the sandbox are unaffected (metadata-only,
  no Java). The capability is fully unit-tested (guard, injected-fake parser, opt-in end-to-end via
  respx, fallback) with no live network.
- **Negative / accepted.** Per-filing extraction is a second HTTP fetch (rate-limited by the same
  per-host bucket). `preexec_fn` is POSIX-only (Linux/macOS) — acceptable for the single-box target;
  Windows would need a different containment. The connector exposes the extraction as a capability
  (`extract_filing`) the SEC fetch path uses when enabled; the building blocks (sandbox + parser) are
  the load-bearing, hostile-input-safe deliverable. Pin the `tika-app.jar` version for deterministic
  output (runbook).
