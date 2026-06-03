"""ORM models. Importing this module registers every table on ``Base.metadata``,
which is what Alembic's ``env.py`` targets for autogenerate. Add new table modules
here as their blocks land (B3: crawl_job/raw_document, B5: document, ...).
"""

from __future__ import annotations

from atlascore.storage.postgres.models.source import Source

__all__ = ["Source"]
