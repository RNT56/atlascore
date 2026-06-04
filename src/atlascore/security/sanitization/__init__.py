"""HTML sanitization (CLAUDE.md §14, docs/security §4).

Fetched HTML is hostile. Before any text is indexed (FTS), embedded, summarized, or exposed
to an agent it must be sanitized. AtlasCore stores parsed bodies as **plain text**
(``document.body_text`` is the FTS source), so the sanitizer takes the strongest allowlist
position: it keeps **no** tags and emits only the visible text, with the contents of
active-content elements (``<script>``/``<style>``/``<iframe>``/…) dropped entirely.

Two layers, because a security boundary must not rely on a lenient parser:

1. **nh3 (ammonia)** — a real HTML5 tokenizer. It removes dangerous tags and the *contents*
   of active-content elements correctly even for adversarial markup (unterminated attribute
   quotes, stray closes, comments, ``<svg><script>``), keeping only an allowlist of
   structural/block tags so block boundaries survive. This closes the tokenizer-mismatch
   leaks a hand-rolled ``html.parser`` pass is prone to.
2. **block-aware text extraction** — a stdlib ``HTMLParser`` pass over nh3's already-safe
   output turns the kept block tags into line breaks and emits the text. A per-element drop
   stack is kept as defense-in-depth (it operates on safe HTML, so it is belt-and-suspenders).

Because no tag, attribute, or URL survives, ``javascript:``/``data:`` links, ``on*`` handlers,
``<iframe>``/``<object>``/``<form>`` and redirect primitives are neutralized by construction.

Known residual (not reachable via the B5 RSS path): nh3 parses fragments in a body context, so
bare text placed *directly* inside ``<head>`` can pass through as inert text (never active
content). Feed items hand the parser an extracted content fragment (no ``<head>``), and a
future HTML connector extracts main content via trafilatura first, so this edge does not arise
in practice. A tag-preserving allowlist sanitizer (for an eventual ``get_raw_document`` HTML
view, B7) is intentionally NOT built here — the MVP only ever exposes sanitized text.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import nh3

# Structural/block tags kept by nh3 so block boundaries survive into line breaks. Inline tags
# (``<strong>``/``<a>``/``<span>``) are NOT kept — nh3 drops the tag but keeps the text.
_KEEP_TAGS = frozenset(
    {
        "p",
        "br",
        "hr",
        "div",
        "li",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "tr",
        "table",
        "thead",
        "tbody",
        "section",
        "article",
        "header",
        "footer",
        "aside",
        "nav",
        "main",
        "blockquote",
        "pre",
        "figure",
        "figcaption",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)

# Elements whose *textual content* nh3 removes entirely (active content, document metadata,
# embedded sub-documents, interactive widgets). MUST be disjoint from _KEEP_TAGS (nh3 rejects
# overlap). The stdlib second pass reuses this set as a defense-in-depth drop stack.
_DROP_CONTENT_TAGS = frozenset(
    {
        "script",
        "style",
        "template",
        "noscript",
        "head",
        "title",
        "svg",
        "math",
        "iframe",
        "frameset",
        "object",
        "applet",
        "form",
        "button",
        "select",
        "option",
        "textarea",
    }
)

_WS_RUN = re.compile(r"[^\S\n]+")  # runs of spaces/tabs, but not newlines
_BLANK_LINES = re.compile(r"\n{2,}")
_SPACE_AROUND_NL = re.compile(r"[^\S\n]*\n[^\S\n]*")


def _nh3_clean(html: str) -> str:
    """First pass: a real HTML5 tokenizer strips dangerous tags + active-content bodies."""
    return nh3.clean(
        html,
        tags=set(_KEEP_TAGS),
        attributes={},  # strip every attribute (no href/src/style/on* survives)
        clean_content_tags=set(_DROP_CONTENT_TAGS),
        strip_comments=True,
    )


class _TextExtractor(HTMLParser):
    """Second pass over nh3-safe HTML: collect text, drop any residual active-content element
    (via a per-element name stack, not a shared counter), and insert line breaks at block
    boundaries. ``convert_charrefs=True`` (the default) decodes entities for us."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._drop_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP_CONTENT_TAGS:
            self._drop_stack.append(tag)
        elif tag in _KEEP_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _KEEP_TAGS:  # e.g. <br/>, <hr/>
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_CONTENT_TAGS:
            # Pop only a MATCHING open drop element; ignore a stray close (no false exit).
            if tag in self._drop_stack:
                while self._drop_stack and self._drop_stack.pop() != tag:
                    pass
        elif tag in _KEEP_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._drop_stack:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def _collapse_whitespace(text: str) -> str:
    """Collapse horizontal whitespace runs, trim spaces around newlines, and squeeze blank
    lines, then strip — so output is stable and golden-test friendly."""
    text = _WS_RUN.sub(" ", text)
    text = _SPACE_AROUND_NL.sub("\n", text)
    text = _BLANK_LINES.sub("\n", text)
    return text.strip()


def strip_html(html: str | None) -> str:
    """Return the visible text of ``html`` with all tags removed and active-content elements
    dropped. Safe to index, embed, summarize, or expose to an agent.

    Input is treated as untrusted: adversarial/malformed markup is tolerated (nh3 is a real
    HTML5 tokenizer) and never executed. Returns ``""`` for ``None``/empty/whitespace-only.
    """
    if not html or not html.strip():
        return ""
    parser = _TextExtractor()
    parser.feed(_nh3_clean(html))
    parser.close()
    return _collapse_whitespace(parser.text())
