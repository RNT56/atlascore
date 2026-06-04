"""A deterministic, dependency-free extractive summarizer (the MVP ``local`` backend).

It takes each document's leading sentence(s) as its summary contribution and cites every claim back
to the **internal** document id it came from. No model download, no network, no spend — so the
summary pipeline runs offline / in CI and is bit-for-bit reproducible (which the R4 cache relies
on). It never invents facts (it only copies already-sanitized body text) and never follows
instructions embedded in the content (it is not an LLM). A real local/hosted LLM is a pluggable
upgrade behind the same interface (ADR 0022).
"""

from __future__ import annotations

import re

from atlascore.enrichment.summaries.base import SummaryInput, SummaryResult

MODEL_NAME = "extractive"
MODEL_VERSION = "1.0.0"
PROMPT_VERSION = "extractive@1"

# Per-document budget: take up to this many leading sentences, capped at this many characters.
_MAX_SENTENCES_PER_DOC = 2
_MAX_CHARS_PER_DOC = 320
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_WS_RE = re.compile(r"\s+")


def _lead_sentences(text: str) -> list[str]:
    """Return up to the leading ``_MAX_SENTENCES_PER_DOC`` sentences, char-capped."""
    normalized = _WS_RE.sub(" ", text).strip()
    if not normalized:
        return []
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(normalized) if s.strip()]
    out: list[str] = []
    used = 0
    for sentence in sentences[:_MAX_SENTENCES_PER_DOC]:
        if used + len(sentence) > _MAX_CHARS_PER_DOC and out:
            break
        out.append(sentence[:_MAX_CHARS_PER_DOC])
        used += len(sentence)
    return out


class ExtractiveSummarizer:
    """Lead-sentence extractive summarizer with per-document citations (deterministic)."""

    @property
    def model_name(self) -> str:
        return MODEL_NAME

    @property
    def model_version(self) -> str:
        return MODEL_VERSION

    @property
    def prompt_version(self) -> str:
        return PROMPT_VERSION

    def summarize(self, docs: list[SummaryInput]) -> SummaryResult:
        citations: dict[str, list[str]] = {}
        lines: list[str] = []
        for doc in docs:
            claims = _lead_sentences(doc.body_text or doc.title or "")
            if not claims:
                continue
            citations[doc.document_id] = claims
            label = (doc.title or "").strip()
            body = " ".join(claims)
            lines.append(f"{label}: {body}" if label else body)
        summary_text = "\n".join(lines)
        # token_cost is a rough word count — local extraction is free, but the field is part of the
        # stored provenance, so it carries an honest estimate rather than 0.
        token_cost = len(summary_text.split())
        return SummaryResult(summary_text=summary_text, citations=citations, token_cost=token_cost)
