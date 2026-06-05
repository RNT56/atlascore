"""spaCy NER backend (B21; behind the ``local-embeddings`` extra, import-guarded).

Maps spaCy entity spans to :class:`EntityCandidate`s. Imported lazily so this module loads with
spaCy absent; :func:`get_ner_backend` falls back to the rule-based extractor if construction fails.
"""

from __future__ import annotations

from atlascore.core.errors import ConfigError
from atlascore.core.settings import Settings, get_settings
from atlascore.enrichment.ner.base import EntityCandidate

# spaCy label → AtlasCore entity_type (only the entity kinds we care about).
_LABEL_MAP = {
    "ORG": "organization",
    "PERSON": "person",
    "GPE": "location",
    "LOC": "location",
    "PRODUCT": "product",
    "EVENT": "event",
}
_DEFAULT_MODEL = "en_core_web_sm"


class SpacyNer:
    """A spaCy small-model NER backend (e.g. en_core_web_sm)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        try:
            import spacy
        except ImportError as exc:  # pragma: no cover - exercised only with the extra installed
            raise ConfigError(
                "NER_BACKEND=spacy needs the `local-embeddings` extra (uv sync --extra local-embeddings)"
            ) from exc
        model = _DEFAULT_MODEL
        try:
            self._nlp = spacy.load(model, disable=["lemmatizer", "tagger", "parser"])
        except OSError as exc:  # pragma: no cover - model not downloaded
            raise ConfigError(
                f"spaCy model {model!r} is not installed (python -m spacy download {model})"
            ) from exc
        self._version = str(getattr(self._nlp, "meta", {}).get("version", "0"))

    @property
    def model_name(self) -> str:
        return "spacy"

    @property
    def model_version(self) -> str:
        return f"{_DEFAULT_MODEL}@{self._version}"

    def extract(self, text: str) -> list[EntityCandidate]:
        if not text:
            return []
        doc = self._nlp(text)
        out: list[EntityCandidate] = []
        seen: set[tuple[str, str]] = set()
        for ent in doc.ents:
            etype = _LABEL_MAP.get(ent.label_)
            if etype is None:
                continue
            name = ent.text.strip()
            normalized = " ".join(name.lower().split())
            key = (etype, normalized)
            if not normalized or key in seen:
                continue
            seen.add(key)
            out.append(
                EntityCandidate(
                    text=name,
                    entity_type=etype,
                    normalized_name=normalized,
                    confidence=0.85,
                )
            )
        return out
