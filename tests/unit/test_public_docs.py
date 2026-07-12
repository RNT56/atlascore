"""Contract tests for the public documentation front door."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_PUBLIC_DOCS = (
    _ROOT / "README.md",
    _ROOT / "docs/getting-started.md",
    _ROOT / "docs/runbooks/README.md",
    _ROOT / "docs/mcp/README.md",
    _ROOT / "infra/systemd/README.md",
    _ROOT / "infra/launchd/README.md",
    _ROOT / "CONTRIBUTING.md",
    _ROOT / "SECURITY.md",
)
_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")


@pytest.mark.parametrize("document", _PUBLIC_DOCS, ids=lambda path: path.name)
def test_local_links_from_public_docs_resolve(document: Path) -> None:
    """A renamed/missing local target must fail before the public front door is merged."""
    missing: list[str] = []
    for raw_target in _MARKDOWN_LINK.findall(document.read_text()):
        target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        resolved = (document.parent / unquote(parsed.path)).resolve()
        if not resolved.exists():
            missing.append(target)
    assert not missing, f"broken local links in {document.relative_to(_ROOT)}: {missing}"


def test_readme_links_complete_setup_and_operations() -> None:
    readme = (_ROOT / "README.md").read_text()
    assert "(docs/getting-started.md)" in readme
    assert "(docs/runbooks/README.md)" in readme
    assert "uv run atlas" in readme
