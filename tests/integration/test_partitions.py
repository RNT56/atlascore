"""Integration: B17 yearly partition topology + routing + the with-rows move (Postgres 16).

Asserts migration 0010's children exist, observations route to the correct yearly child (tableoid),
get_macro_series reads transparently across partitions, ensure_yearly_partitions is idempotent, and
the detach/move/attach dance correctly relocates rows already living in DEFAULT.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from atlascore.connectors_sdk.types import Observation, ParsedSeries
from atlascore.storage.partitions import (
    ensure_yearly_partitions,
    list_existing_partitions,
    partition_name,
)
from atlascore.storage.timeseries import read_macro_series, upsert_observations
from atlascore.worker.partitions import run_partition_maintenance

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)  # migration carved current+next from the same clock domain
CUR = NOW.year
FAR_YEAR = CUR + 5  # uncarved year used by the with-rows move test
PROVIDER = "part_it"
SERIES = "PARTCPI"


def _cleanup(session: Session) -> None:
    session.execute(text("DELETE FROM time_series_point WHERE provider = :p"), {"p": PROVIDER})
    # Drop the with-rows-test partition so the move is exercised fresh on every (re-)run.
    session.execute(text(f"DROP TABLE IF EXISTS {partition_name(FAR_YEAR)}"))


def _obs(year: int, value: str) -> Observation:
    return Observation(ts=datetime(year, 6, 15, tzinfo=UTC), value=Decimal(value), vintage=None)


def test_migration_created_current_and_next_year(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        children = list_existing_partitions(session)
    assert partition_name(CUR) in children
    assert partition_name(CUR + 1) in children
    assert "time_series_point_default" in children


def test_observations_route_to_correct_child_and_read_back(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        upsert_observations(
            session,
            ParsedSeries(
                provider=PROVIDER,
                series_id=SERIES,
                unit="Index",
                observations=[_obs(CUR, "100.0"), _obs(CUR + 1, "101.0")],
            ),
        )

    with Session(migrated_engine) as session:
        # Each row sits in its year's child partition (tableoid::regclass).
        rows = session.execute(
            text(
                "SELECT extract(year from ts)::int AS y, tableoid::regclass::text AS part "
                "FROM time_series_point WHERE provider = :p ORDER BY ts"
            ),
            {"p": PROVIDER},
        ).all()
        by_year = dict(rows)
        assert by_year[CUR] == partition_name(CUR)
        assert by_year[CUR + 1] == partition_name(CUR + 1)

        # get_macro_series reads seamlessly across both partitions.
        series = read_macro_series(session, provider=PROVIDER, series_id=SERIES)
        assert series is not None and len(series.points) == 2

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_with_rows_move_out_of_default_and_idempotent(migrated_engine: Engine) -> None:
    far_year = FAR_YEAR  # uncarved → lands in DEFAULT initially
    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)
        upsert_observations(
            session,
            ParsedSeries(
                provider=PROVIDER,
                series_id=SERIES,
                unit="Index",
                observations=[_obs(far_year, "200.0")],
            ),
        )
    with Session(migrated_engine) as session:
        where = session.scalar(
            text(
                "SELECT tableoid::regclass::text FROM time_series_point "
                "WHERE provider = :p AND extract(year from ts)::int = :y"
            ),
            {"p": PROVIDER, "y": far_year},
        )
        assert where == "time_series_point_default"  # starts in DEFAULT

    # Carve up to far_year: the with-rows dance moves the row into the new bounded child.
    with Session(migrated_engine) as session, session.begin():
        result = ensure_yearly_partitions(session, now=NOW, years_ahead=5)
    assert partition_name(far_year) in result.created

    with Session(migrated_engine) as session:
        where = session.scalar(
            text(
                "SELECT tableoid::regclass::text FROM time_series_point "
                "WHERE provider = :p AND extract(year from ts)::int = :y"
            ),
            {"p": PROVIDER, "y": far_year},
        )
        assert where == partition_name(far_year)  # moved out of DEFAULT into the bounded child

    # Idempotent: re-running creates nothing.
    with Session(migrated_engine) as session, session.begin():
        again = ensure_yearly_partitions(session, now=NOW, years_ahead=5)
    assert again.created == []

    with Session(migrated_engine) as session, session.begin():
        _cleanup(session)


def test_run_partition_maintenance_advisory_locked(migrated_engine: Engine) -> None:
    # The worker entry point runs under the advisory lock; idempotent on an already-carved DB.
    result = run_partition_maintenance(migrated_engine, years_ahead=1)
    assert result.created == []  # current+next already exist from the migration
