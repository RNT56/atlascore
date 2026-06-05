"""The filing parser: raw filing bytes (PDF/HTML) → sandboxed Tika text → sanitized body (B22).

Deterministic given the same Tika version (pin the jar in the runbook). The :class:`TikaClient` is
dependency-injected so the parser is unit-testable with a fake (no Java); when Tika is unavailable the
SEC connector simply never calls this (metadata-only fallback). The extracted text is run through the
same control-char/whitespace cleanup used elsewhere so a poisoned filing can't smuggle active content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from atlascore.security.sandbox.tika import TikaClient

# Pinned parser version, written onto every document.parser_version produced from a filing.
PARSER_VERSION = "filing@1.0.0"

# Strip ASCII control chars except tab/newline/carriage-return (defensive cleanup of extracted text).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RUN_RE = re.compile(r"[ \t]{2,}")


@dataclass(frozen=True)
class ParsedFiling:
    """A parsed filing: cleaned plain-text body + the parser version that produced it."""

    body_text: str
    parser_version: str = PARSER_VERSION
    char_count: int = 0


class FilingParser:
    """Turn raw filing bytes into a sanitized plain-text body via a sandboxed Tika client."""

    parser_version = PARSER_VERSION

    def __init__(self, tika: TikaClient | None = None) -> None:
        self._tika = tika or TikaClient()

    def parse(self, data: bytes, *, content_type: str | None = None) -> ParsedFiling:
        """Extract + clean the filing text. Raises if Tika is unavailable (caller guards)."""
        raw = self._tika.extract_text(data, content_type=content_type)
        body = _clean(raw)
        return ParsedFiling(body_text=body, char_count=len(body))


def _clean(text: str) -> str:
    cleaned = _CONTROL_RE.sub("", text)
    cleaned = _WS_RUN_RE.sub(" ", cleaned)
    # Collapse 3+ blank lines and trim trailing whitespace per line.
    lines = [line.rstrip() for line in cleaned.splitlines()]
    out: list[str] = []
    blanks = 0
    for line in lines:
        if line:
            blanks = 0
            out.append(line)
        else:
            blanks += 1
            if blanks <= 1:
                out.append("")
    return "\n".join(out).strip()
