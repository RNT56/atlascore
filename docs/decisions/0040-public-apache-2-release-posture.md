# 0040. Publish AtlasCore code under Apache-2.0

- **Status:** Accepted
- **Date:** 2026-07-12

## Context

The GitHub repository became public while its license and documentation still described confidential,
private-only code. That gave readers no open-source grant and contradicted the intended code-not-data
publication boundary.

## Decision

AtlasCore code is licensed under Apache License 2.0 and versioned `0.1.0`. Public metadata points to
the real repository. Harvested payloads, normalized data, credentials, operator configuration, and
backups remain outside the repository and are not redistributed.

## Consequences

- Contributors and users receive explicit copyright and patent grants under Apache-2.0.
- Publication does not change the internal-only data policy or source-specific obligations.
- Public repository security controls and CI are release requirements.
