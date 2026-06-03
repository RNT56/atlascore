"""Job-queue vocabulary: job types and the status machine (docs/schemas §2.2)."""

from __future__ import annotations

from enum import StrEnum


class JobType(StrEnum):
    """The kind of work a `crawl_job` represents."""

    FETCH = "fetch"
    PARSE = "parse"
    ENRICH = "enrich"
    ALERT = "alert"
    FRESHNESS = "freshness"


class JobStatus(StrEnum):
    """The `crawl_job` status machine. ``dead`` is the DLQ (over ``max_attempts``)."""

    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    DONE = "done"
    # Reserved (in the spec's status set + the CHECK constraint). The queue's fail() path
    # transitions retry→pending or exhausted→dead; an explicit non-retryable terminal
    # `failed` may be used by a later block.
    FAILED = "failed"
    DEAD = "dead"

    @property
    def is_terminal(self) -> bool:
        """``done``/``dead`` are terminal; the queue never re-claims them."""
        return self in (JobStatus.DONE, JobStatus.DEAD)
