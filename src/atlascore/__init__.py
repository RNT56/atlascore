"""AtlasCore — a governed, single-operator data fabric.

Import root for the whole project. The physical source layout is ``src/atlascore``;
the historical spec grouping of ``packages/`` (libraries) vs ``apps/`` (runnable
roles) is preserved as the subpackage names directly under this package:

- libraries (formerly ``packages/``): ``core``, ``schemas``, ``connectors_sdk``,
  ``connectors``, ``parsers``, ``enrichment``, ``storage``, ``security``, ``alerts``.
- runnable roles (formerly ``apps/``): ``cli``, ``mcp_server``, ``worker``,
  ``scheduler``, ``api``.

So a spec/doc reference such as ``packages/security/secrets`` maps to
``atlascore.security.secrets`` and ``apps/cli`` maps to ``atlascore.cli``.
See ADR 0012 (packaging layout) and CLAUDE.md §7.
"""

from __future__ import annotations

__all__ = ["__version__"]

# Kept in sync with [project].version in pyproject.toml. The installed
# distribution version is the runtime source of truth (see atlascore.cli.main).
__version__ = "0.0.0"
