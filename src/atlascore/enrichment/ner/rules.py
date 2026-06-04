"""Deterministic, dependency-free rule-based NER (the MVP NER backend).

Extracts three kinds of entity candidate, no model download required (so it runs offline / in CI
and is reproducible for golden tests):

1. **Tickers** — ``(NASDAQ: AAPL)`` / ``(NYSE:XYZ)`` exchange-qualified symbols and ``$AAPL``
   cashtags → ``{"ticker": "AAPL"}``, type ``company``.
2. **CIK numbers** — ``CIK 0000320193`` / ``CIK#320193`` → ``{"cik": "0000320193"}`` (zero-padded
   to 10), type ``company``.
3. **Proper-noun phrases** — runs of Capitalized words (with internal connectors like *of*/*and*/
   *&*), classified by corporate-suffix / government-keyword heuristics, else a generic
   ``proper_noun``.

This is a precision-over-recall baseline; a spaCy small-model backend is a pluggable upgrade
(ADR 0021). Confidence is the extractor's heuristic certainty, never a fabricated fact.
"""

from __future__ import annotations

import re

from atlascore.enrichment.ner.base import EntityCandidate

MODEL_NAME = "rule-ner"
MODEL_VERSION = "1.0.0"

_EXCHANGES = "NYSE|NASDAQ|NYSEARCA|AMEX|OTC|LSE|TSX"
_TICKER_PAREN_RE = re.compile(rf"\((?:{_EXCHANGES})\s*:\s*([A-Z]{{1,6}})\)")
_CASHTAG_RE = re.compile(r"\$([A-Z]{1,6})\b")
_CIK_RE = re.compile(r"\bCIK\s*#?\s*(\d{1,10})\b", re.IGNORECASE)

# A run of capitalized tokens, allowing a few lowercase connector words and ``&`` between them.
# Deliberately conservative: "and"/"the" are NOT connectors (they merge distinct entities), so
# "Goldman Sachs and the Department of Justice" splits into two candidates.
_PHRASE_RE = re.compile(r"[A-Z][\w&.'’-]*(?: (?:of|for|de|&|[A-Z][\w&.'’-]*))*")  # noqa: RUF001 - curly apostrophe + ampersand are intentional in names
_CONNECTORS = frozenset({"of", "for", "de", "&"})
# Split text into sentences first so a phrase never spans a sentence boundary ("…SEC. The Fed…").
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

_CORP_SUFFIXES = frozenset(
    {
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "ltd",
        "limited",
        "llc",
        "llp",
        "lp",
        "plc",
        "group",
        "holdings",
        "partners",
        "bank",
        "capital",
        "ventures",
        "technologies",
        "systems",
        "industries",
        "labs",
        "ag",
        "sa",
        "nv",
    }
)
_GOV_KEYWORDS = frozenset(
    {
        "reserve",
        "federal",
        "commission",
        "department",
        "authority",
        "bureau",
        "agency",
        "administration",
        "committee",
        "treasury",
        "ministry",
        "senate",
        "congress",
        "council",
        "board",
        "office",
        "service",
    }
)

# Capitalized words that begin sentences / are function words / calendar terms — dropped when a
# proper-noun chunk is a single one of them, to cut noise. (Multi-word chunks are kept.)
_STOPWORDS = frozenset(
    {
        "the",
        "this",
        "that",
        "these",
        "those",
        "a",
        "an",
        "we",
        "it",
        "is",
        "in",
        "on",
        "at",
        "as",
        "by",
        "for",
        "from",
        "to",
        "of",
        "and",
        "but",
        "or",
        "he",
        "she",
        "they",
        "you",
        "i",
        "if",
        "when",
        "while",
        "after",
        "before",
        "during",
        "our",
        "their",
        "his",
        "her",
        "its",
        "read",
        "more",
        "see",
        "also",
        "however",
        "meanwhile",
        "today",
        "yesterday",
        "tomorrow",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "nyse",
        "nasdaq",
        "amex",
        "otc",
        "cik",
        "mr",
        "ms",
        "mrs",
        "dr",
    }
)


def normalize_name(name: str) -> str:
    """Fold a surface form to a canonical lookup key (lowercase, trimmed, no possessive)."""
    cleaned = name.strip().strip(".,;:!?\"'()[]").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"['’]s$", "", cleaned)  # noqa: RUF001 — strip a possessive smart apostrophe
    return cleaned.lower()


def _pad_cik(raw: str) -> str:
    return raw.lstrip("0").zfill(10) if raw.strip("0") else "0" * 10


def _classify(words: list[str]) -> tuple[str, float]:
    """Return (entity_type, confidence) for a trimmed proper-noun phrase."""
    lowered = {w.rstrip(".").lower() for w in words}
    if lowered & _CORP_SUFFIXES:
        return "company", 0.8
    if lowered & _GOV_KEYWORDS:
        return "organization", 0.75
    return "proper_noun", 0.6 if len(words) > 1 else 0.5


class RuleBasedNer:
    """Capitalized-phrase + ticker/CIK pattern extractor (deterministic)."""

    @property
    def model_name(self) -> str:
        return MODEL_NAME

    @property
    def model_version(self) -> str:
        return MODEL_VERSION

    def extract(self, text: str) -> list[EntityCandidate]:
        if not text:
            return []
        # normalized_name -> candidate, accumulating identifiers + keeping the best confidence.
        found: dict[str, EntityCandidate] = {}

        def _add(cand: EntityCandidate) -> None:
            existing = found.get(cand.normalized_name)
            if existing is None:
                found[cand.normalized_name] = cand
                return
            merged = {**existing.identifiers, **cand.identifiers}
            best = existing if existing.confidence >= cand.confidence else cand
            found[cand.normalized_name] = EntityCandidate(
                text=best.text,
                entity_type=best.entity_type,
                normalized_name=best.normalized_name,
                confidence=max(existing.confidence, cand.confidence),
                identifiers=merged,
            )

        for ticker in [*_TICKER_PAREN_RE.findall(text), *_CASHTAG_RE.findall(text)]:
            _add(
                EntityCandidate(
                    text=ticker,
                    entity_type="company",
                    normalized_name=ticker.lower(),
                    confidence=0.9,
                    identifiers={"ticker": ticker},
                )
            )

        for cik in _CIK_RE.findall(text):
            padded = _pad_cik(cik)
            _add(
                EntityCandidate(
                    text=f"CIK {padded}",
                    entity_type="company",
                    normalized_name=f"cik:{padded}",
                    confidence=0.85,
                    identifiers={"cik": padded},
                )
            )

        for sentence in _SENTENCE_SPLIT_RE.split(text):
            for match in _PHRASE_RE.finditer(sentence):
                words = match.group(0).split()
                # Trim leading sentence-initial / function words ("The Federal Reserve" ->
                # "Federal Reserve"; "See CIK" -> "CIK") and leading/trailing connectors.
                while words and (words[0].lower() in _STOPWORDS or words[0].lower() in _CONNECTORS):
                    words.pop(0)
                while words and words[-1].lower() in _CONNECTORS:
                    words.pop()
                if not words:
                    continue
                surface = " ".join(words)
                normalized = normalize_name(surface)
                if len(normalized) < 2:
                    continue
                entity_type, confidence = _classify(words)
                # Precision over recall: a single-word *generic* proper noun (no corporate/gov
                # signal, no identifier) is too noisy to treat as an entity ("Analysts", "SEC").
                # Single-word candidates survive only as company/organization or via ticker/CIK.
                if len(words) == 1 and entity_type == "proper_noun":
                    continue
                _add(
                    EntityCandidate(
                        text=surface,
                        entity_type=entity_type,
                        normalized_name=normalized,
                        confidence=confidence,
                    )
                )

        return list(found.values())
