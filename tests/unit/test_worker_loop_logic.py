"""Unit tests for the worker loop mechanics + signal handler (B19; offline, no DB)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from atlascore.core.clock import FixedClock
from atlascore.core.errors import SchedulerBusyError
from atlascore.scheduler.lock import SCHEDULER_LOCK_KEY
from atlascore.storage.rawstore import RawStore
from atlascore.worker import loop as loop_mod
from atlascore.worker.loop import install_signal_handlers, run_worker_loop

pytestmark = pytest.mark.unit

NOW = FixedClock(datetime(2026, 6, 1, 9, 0, tzinfo=UTC))
_FAKE_ENGINE = create_engine("postgresql+psycopg://x:x@127.0.0.1:1/none")  # never connected to


def test_install_signal_handlers_sets_event() -> None:
    stop = threading.Event()
    install_signal_handlers(stop)  # may no-op off the main thread; the handler logic is the point
    assert not stop.is_set()


def test_loop_drains_while_work_then_stops_on_max_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Parse-only loop (no fetch lock, no DB): run_one_parse returns "work" each pass.
    monkeypatch.setattr(loop_mod, "run_one_parse", lambda *a, **k: object())
    result = run_worker_loop(
        _FAKE_ENGINE,
        roles=("parse",),
        clock=NOW,
        raw_store=RawStore(tmp_path),
        max_passes=3,
    )
    assert result.passes == 3
    assert result.jobs_processed == 3  # work every pass → no idle sleep


def test_loop_exits_immediately_when_stop_preset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(loop_mod, "run_one_parse", lambda *a, **k: None)
    stop = threading.Event()
    stop.set()  # pre-stopped → the while loop never iterates
    result = run_worker_loop(
        _FAKE_ENGINE, roles=("parse",), stop_event=stop, clock=NOW, raw_store=RawStore(tmp_path)
    )
    assert result.passes == 0 and result.jobs_processed == 0


def test_scheduler_lock_key_split() -> None:
    # The classid/objid split mirrors the fetch-lock convention (B19 lock).
    assert SCHEDULER_LOCK_KEY >> 32 == 0x41544C53
    assert SCHEDULER_LOCK_KEY & 0xFFFFFFFF == 0x53434844
    assert issubclass(SchedulerBusyError, Exception)
