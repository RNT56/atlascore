"""Partition-maintenance job (B17): create yearly ``time_series_point`` partitions ahead of time.

Runs the idempotent :func:`storage.partitions.ensure_yearly_partitions` under a pg advisory lock so
two schedulers can never race the detach/attach dance. The scheduler (B19) enqueues this as a
``JobType.PARTITION`` job on a monthly cadence; until then it is operator-run via
``atlas tsp partitions ensure`` (or a cron).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from atlascore.core.clock import Clock, SystemClock
from atlascore.core.errors import AtlasError
from atlascore.core.logging import get_logger
from atlascore.security.secrets import sanitize_for_storage
from atlascore.storage.partitions import PartitionMaintenanceResult, ensure_yearly_partitions

_log = get_logger("atlascore.worker.partitions")

# Advisory-lock key for partition maintenance ("ATLSPART").
PARTITION_LOCK_KEY = 0x41544C53_50415254


class PartitionMaintenanceBusyError(AtlasError):
    """Raised when another partition-maintenance run already holds the advisory lock."""


@contextmanager
def partition_lock(engine: Engine) -> Iterator[None]:
    """Hold the partition advisory lock for the maintenance window (serializes detach/attach)."""
    conn = engine.connect()
    try:
        acquired = conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": PARTITION_LOCK_KEY}
        ).scalar()
        if not acquired:
            raise PartitionMaintenanceBusyError("another partition-maintenance run holds the lock")
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": PARTITION_LOCK_KEY})
            conn.commit()
    finally:
        conn.close()


def run_partition_maintenance(
    engine: Engine, *, clock: Clock | None = None, years_ahead: int = 1
) -> PartitionMaintenanceResult:
    """Ensure current..current+years_ahead yearly partitions exist (advisory-locked, one txn)."""
    clock = clock or SystemClock()
    try:
        with partition_lock(engine), Session(engine) as session, session.begin():
            return ensure_yearly_partitions(session, now=clock.now(), years_ahead=years_ahead)
    except PartitionMaintenanceBusyError:
        raise
    except Exception as exc:  # service boundary: never leak a DSN; surface a clean result
        _log.warning("partition_maintenance_failed", error=sanitize_for_storage(str(exc)))
        return PartitionMaintenanceResult(created=[])
