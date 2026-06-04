"""ORM models. Importing this module registers every table on ``Base.metadata``,
which is what Alembic's ``env.py`` targets for autogenerate. Add new table modules
here as their blocks land (B3: crawl_job/raw_document, B5: document, ...).
"""

from __future__ import annotations

from atlascore.storage.postgres.models.audit_log import AuditLog
from atlascore.storage.postgres.models.crawl_job import CrawlJob
from atlascore.storage.postgres.models.document import Document
from atlascore.storage.postgres.models.raw_document import RawDocument
from atlascore.storage.postgres.models.source import Source
from atlascore.storage.postgres.models.source_health import SourceHealth

__all__ = ["AuditLog", "CrawlJob", "Document", "RawDocument", "Source", "SourceHealth"]
