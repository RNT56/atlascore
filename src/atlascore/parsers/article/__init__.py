"""Article parser: HTML -> canonical ``document`` text + attribution (trafilatura + fallback)."""

from __future__ import annotations

from atlascore.parsers.article.parser import PARSER_VERSION, ArticleParser, ParsedArticle

__all__ = ["PARSER_VERSION", "ArticleParser", "ParsedArticle"]
