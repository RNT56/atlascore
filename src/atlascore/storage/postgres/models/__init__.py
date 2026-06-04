"""ORM models. Importing this module registers every table on ``Base.metadata``,
which is what Alembic's ``env.py`` targets for autogenerate. Add new table modules
here as their blocks land (B3: crawl_job/raw_document, B5: document, ...).
"""

from __future__ import annotations

from atlascore.storage.postgres.models.audit_log import AuditLog
from atlascore.storage.postgres.models.crawl_job import CrawlJob
from atlascore.storage.postgres.models.document import Document
from atlascore.storage.postgres.models.embedding import Embedding
from atlascore.storage.postgres.models.entity import Entity
from atlascore.storage.postgres.models.mention import Mention
from atlascore.storage.postgres.models.raw_document import RawDocument
from atlascore.storage.postgres.models.source import Source
from atlascore.storage.postgres.models.source_health import SourceHealth
from atlascore.storage.postgres.models.summary import Summary
from atlascore.storage.postgres.models.time_series_point import TimeSeriesPoint

__all__ = [
    "AuditLog",
    "CrawlJob",
    "Document",
    "Embedding",
    "Entity",
    "Mention",
    "RawDocument",
    "Source",
    "SourceHealth",
    "Summary",
    "TimeSeriesPoint",
]
