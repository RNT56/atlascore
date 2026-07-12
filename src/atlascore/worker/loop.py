"""The long-running worker drain loop (B19): fetch → parse → enrich, unattended.

Continuously claims one job per enabled role per pass via the existing ``run_one_*`` functions
(``SELECT … FOR UPDATE SKIP LOCKED``), looping immediately while there is work and sleeping with a
bounded idle backoff when idle (``stop_event.wait`` so SIGINT/SIGTERM wakes it instantly). The
**fetch** role holds the single-fetch advisory lock for the loop's lifetime (R16); parse/enrich need
no lock (they scale out). Stale claims are periodically reclaimed to ``pending`` (crash safety, §17).

Periodic control-plane housekeeping (freshness/alert/partition/audit) lives in the scheduler, not
here — this loop is the per-item pipeline drainer.
"""

from __future__ import annotations

import contextlib
import signal
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

import httpx
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from atlascore.connectors_sdk import SafeFetcher
from atlascore.core.clock import Clock, SystemClock
from atlascore.core.logging import get_logger
from atlascore.core.settings import Settings, get_settings
from atlascore.storage.queue import DEFAULT_VISIBILITY_TIMEOUT, reclaim_stale
from atlascore.storage.rawstore import RawStore
from atlascore.worker.enrich import run_one_enrich
from atlascore.worker.parse import run_one_parse
from atlascore.worker.run import fetch_worker_lock, run_one_fetch

_log = get_logger("atlascore.worker.loop")

ALL_ROLES = ("fetch", "parse", "enrich")


@dataclass(frozen=True)
class WorkerLoopResult:
    passes: int
    jobs_processed: int


def install_signal_handlers(stop_event: threading.Event) -> None:
    """Set ``stop_event`` on SIGINT/SIGTERM for graceful shutdown (best-effort; main thread only)."""

    def _handler(_signum: int, _frame: object) -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        # Not in the main thread (e.g. tests) → caller drives stop_event directly.
        with contextlib.suppress(ValueError):
            signal.signal(sig, _handler)


@contextmanager
def _fetch_role(
    engine: Engine, *, enabled: bool, injected: SafeFetcher | None
) -> Iterator[SafeFetcher | None]:
    """Hold the single-fetch lock for the loop iff the fetch role is enabled, yielding a fetcher.

    Production builds its own httpx-backed ``SafeFetcher``; tests pass ``injected`` (e.g. with a
    stubbed resolver) — the lock is still acquired so the single-fetch invariant is exercised.
    """
    if not enabled:
        yield None
        return
    with fetch_worker_lock(engine):
        if injected is not None:
            yield injected
            return
        settings = get_settings()
        with httpx.Client(trust_env=False) as client:
            yield SafeFetcher(
                client=client, clock=SystemClock(), user_agent=settings.default_user_agent
            )


def run_worker_loop(
    engine: Engine,
    *,
    roles: tuple[str, ...] = ALL_ROLES,
    stop_event: threading.Event | None = None,
    clock: Clock | None = None,
    settings: Settings | None = None,
    raw_store: RawStore | None = None,
    fetcher: SafeFetcher | None = None,
    max_passes: int | None = None,
) -> WorkerLoopResult:
    """Drain fetch/parse/enrich until ``stop_event`` is set. ``max_passes`` bounds it for tests."""
    clock = clock or SystemClock()
    settings = settings or get_settings()
    stop = stop_event or threading.Event()
    raw_store = raw_store or RawStore.from_settings()
    fetch_on = "fetch" in roles
    backoff = 1.0
    passes = processed = 0
    reclaim_every = DEFAULT_VISIBILITY_TIMEOUT.total_seconds() / 2
    last_reclaim = clock.now()

    with ExitStack() as stack:
        fetcher = stack.enter_context(_fetch_role(engine, enabled=fetch_on, injected=fetcher))
        _log.info("worker_loop_started", roles=list(roles))
        while not stop.is_set():
            did_work = False
            if fetch_on and fetcher is not None:
                did_work |= (
                    run_one_fetch(engine, fetcher=fetcher, raw_store=raw_store, clock=clock)
                    is not None
                )
            if "parse" in roles:
                did_work |= run_one_parse(engine, raw_store=raw_store, clock=clock) is not None
            if "enrich" in roles:
                did_work |= run_one_enrich(engine, clock=clock) is not None

            now = clock.now()
            if (now - last_reclaim).total_seconds() >= reclaim_every:
                with Session(engine) as session, session.begin():
                    reclaim_stale(session, now=now)
                last_reclaim = now

            passes += 1
            if did_work:
                processed += 1
                backoff = 1.0  # work pending → keep draining immediately
            else:
                # min(...) caps even the first idle sleep, so a tiny configured max keeps tests fast.
                stop.wait(min(backoff, settings.worker_idle_backoff_max_seconds))
                backoff = min(backoff * 2, settings.worker_idle_backoff_max_seconds)
            if max_passes is not None and passes >= max_passes:
                break
        _log.info("worker_loop_stopped", passes=passes, jobs_processed=processed)
    return WorkerLoopResult(passes=passes, jobs_processed=processed)
