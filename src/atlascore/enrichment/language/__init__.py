"""Lightweight language detection (CLAUDE.md §8, docs/schemas §2.4 ``document.language``).

The article parser (B5, trafilatura) already sets ``document.language`` for most documents, so the
enrichment language step is a thin confirm/fallback: prefer the parser's value, else apply a tiny
English-stopword heuristic, else leave it ``None`` (never fabricate a language). A full langid model
is a later upgrade — it would slot in here behind the same function signature.
"""

from __future__ import annotations

import re

# A handful of very common English function words — enough for a cheap "looks like English" guess
# without a model. Absence of a hit returns ``None`` (honest unknown), not a fabricated language.
_EN_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "of",
        "to",
        "in",
        "is",
        "that",
        "for",
        "with",
        "as",
        "on",
        "by",
        "this",
        "are",
        "was",
        "be",
        "from",
        "at",
        "it",
        "has",
        "have",
        "will",
    }
)
_WORD_RE = re.compile(r"[a-z']+")


def detect_language(text: str | None, *, fallback: str | None = None) -> str | None:
    """Return a best-effort language code, preferring ``fallback`` (the parser's value).

    Returns ``fallback`` when set; otherwise ``"en"`` if enough common English words appear in
    ``text``; otherwise ``None`` (unknown — never guessed).
    """
    if fallback:
        return fallback
    if not text:
        return None
    words = _WORD_RE.findall(text.lower())
    if len(words) < 5:
        return None
    hits = sum(1 for w in words if w in _EN_STOPWORDS)
    return "en" if hits / len(words) >= 0.1 else None


__all__ = ["detect_language"]
