"""Unit tests for the pure freshness status classifier (docs/runbooks §8). No DB."""

from __future__ import annotations

from datetime import timedelta

import pytest

from atlascore.schemas.source_health import HealthStatus
from atlascore.worker.freshness import classify_status

pytestmark = pytest.mark.unit

WARN = timedelta(hours=36)
ERROR = timedelta(hours=72)


def test_ok_when_fresh() -> None:
    assert (
        classify_status(timedelta(hours=10), 0, warn_after=WARN, error_after=ERROR)
        is HealthStatus.OK
    )


def test_warn_between_thresholds() -> None:
    assert (
        classify_status(timedelta(hours=40), 0, warn_after=WARN, error_after=ERROR)
        is HealthStatus.WARN
    )


def test_stale_past_error_threshold() -> None:
    assert (
        classify_status(timedelta(days=10), 0, warn_after=WARN, error_after=ERROR)
        is HealthStatus.STALE
    )


def test_cooling_down_takes_precedence_over_lag() -> None:
    # Even with a stale lag, hitting the failure threshold means the breaker is open.
    assert (
        classify_status(timedelta(days=10), 3, warn_after=WARN, error_after=ERROR)
        is HealthStatus.COOLING_DOWN
    )


def test_error_when_failed_and_never_produced_data() -> None:
    assert classify_status(None, 2, warn_after=WARN, error_after=ERROR) is HealthStatus.ERROR


def test_ok_when_never_run_and_no_failures() -> None:
    assert classify_status(None, 0, warn_after=WARN, error_after=ERROR) is HealthStatus.OK


def test_no_thresholds_means_lag_cannot_warn() -> None:
    # Missing thresholds (unparseable freshness) -> lag is not classified, only failures are.
    assert (
        classify_status(timedelta(days=10), 0, warn_after=None, error_after=None) is HealthStatus.OK
    )


def test_one_failure_with_fresh_data_stays_ok() -> None:
    assert (
        classify_status(timedelta(hours=1), 1, warn_after=WARN, error_after=ERROR)
        is HealthStatus.OK
    )


def test_custom_cooling_threshold() -> None:
    assert (
        classify_status(None, 2, warn_after=WARN, error_after=ERROR, cooling_threshold=2)
        is HealthStatus.COOLING_DOWN
    )
