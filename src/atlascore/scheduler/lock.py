"""The single-scheduler advisory lock (§17, CLAUDE.md §6 single-scheduler invariant).

Mirrors ``worker.run.fetch_worker_lock``/``fetch_worker_active`` but with a DISTINCT key so the
scheduler and the fetch worker never collide. Two scheduler instances would double-fire jobs, so the
second is refused at startup with :class:`SchedulerBusyError`. ``scheduler_active`` is a read-only
liveness probe for ``atlas health``/``atlas digest`` (it inspects ``pg_locks`` without acquiring).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from atlascore.core.errors import SchedulerBusyError

# Advisory-lock key for the single scheduler role ("ATLSSCHD").
SCHEDULER_LOCK_KEY = 0x41544C53_53434844


def scheduler_active(session: Session) -> bool:
    """True if some process currently holds the scheduler advisory lock (read-only, like fetch)."""
    held = session.scalar(
        text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND classid = :hi AND objid = :lo AND objsubid = 1"
        ),
        {"hi": SCHEDULER_LOCK_KEY >> 32, "lo": SCHEDULER_LOCK_KEY & 0xFFFFFFFF},
    )
    return bool(held)


@contextmanager
def scheduler_lock(engine: Engine) -> Iterator[None]:
    """Hold the single-scheduler advisory lock for the scheduler's lifetime; refuse a second holder."""
    conn = engine.connect()
    try:
        acquired = conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": SCHEDULER_LOCK_KEY}
        ).scalar()
        if not acquired:
            raise SchedulerBusyError(
                "another scheduler instance holds the lock (single-scheduler invariant, §17)"
            )
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": SCHEDULER_LOCK_KEY})
            conn.commit()
    finally:
        conn.close()
