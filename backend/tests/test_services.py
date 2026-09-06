"""Service-layer behaviour: the scorecard loop, the guidance rules, and the
repository upserts that make re-running a job safe.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db.models import Estimator, Forecast, Measurement, ModelVersion
from app.domain.geo import Point
from app.repositories.forecasts import ForecastRepository
from app.repositories.measurements import MeasurementRepository
from app.repositories.registry import ModelRepository
from app.schemas.common import AqiSchema, ForecastPointSchema, ForecastSchema
from app.services.guidance import GuidanceService, Sensitivity
from app.services.scoring import ScoringService

# --- idempotent ingestion -----------------------------------------------


def test_re_ingesting_the_same_hour_updates_rather_than_duplicates(
    session, station_factory
):
    """Scheduled runs overlap on purpose, so every write has to be repeatable."""
    from app.providers.base import MeasurementRecord

    station = station_factory()
    repository = MeasurementRepository(session)
    when = dt.datetime(2026, 9, 1, 10, tzinfo=dt.UTC)

    repository.upsert_from_records(station.id, [MeasurementRecord("pm25", when, 40.0)])
    repository.upsert_from_records(station.id, [MeasurementRecord("pm25", when, 47.5)])
    session.flush()

    rows = repository.series(
        station.id, "pm25", when, when + dt.timedelta(hours=1)
    )
    assert len(rows) == 1, "the natural key must collapse the duplicate"
    assert rows[0].value == 47.5, "a corrected reading should win"


def test_two_providers_reporting_the_same_hour_do_not_collide(session, station_factory):
    from app.providers.base import MeasurementRecord

    station = station_factory()
    repository = MeasurementRepository(session)
    when = dt.datetime(2026, 9, 1, 10, tzinfo=dt.UTC)

    written = repository.upsert_from_records(
        station.id,
        [MeasurementRecord("pm25", when, 40.0), MeasurementRecord("pm25", when, 41.0)],
    )
    session.flush()

    assert written == 1, "the batch is de-duplicated before it reaches Postgres"


def test_a_negative_reading_is_dropped_at_the_repository_boundary(
    session, station_factory
):
    from app.providers.base import MeasurementRecord

    station = station_factory()
    repository = MeasurementRepository(session)
    when = dt.datetime(2026, 9, 1, 10, tzinfo=dt.UTC)

    repository.upsert_from_records(station.id, [MeasurementRecord("pm25", when, -999.0)])
    session.flush()

    assert repository.series(station.id, "pm25", when, when + dt.timedelta(hours=1)) == []


def test_nearest_stations_are_ordered_by_real_distance(session, station_factory):
    from app.repositories.stations import StationRepository

    station_factory(name="Far", latitude=23.95, longitude=90.55)
    station_factory(name="Near", latitude=23.82, longitude=90.42)
    session.flush()

    found = StationRepository(session).near(Point(23.81, 90.41), radius_km=40)

    assert [station.name for station, _ in found] == ["Near", "Far"]
    assert found[0][1] < found[1][1]


def test_a_station_outside_the_radius_is_excluded(session, station_factory):
    from app.repositories.stations import StationRepository

    station_factory(name="Delhi", latitude=28.61, longitude=77.20)
    session.flush()

    assert StationRepository(session).near(Point(23.81, 90.41), radius_km=30) == []


def test_a_silent_station_is_retired_not_deleted(session, station_factory):
    """Its history is still valid training data."""
    from app.repositories.stations import StationRepository

    station = station_factory(
        last_seen_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    )
    session.flush()

    retired = StationRepository(session).mark_stale(
        dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    )
    session.flush()

    assert retired == 1
    assert session.get(type(station), station.id) is not None
    assert station.is_active is False


# --- the model registry --------------------------------------------------


def _version(session, number: int, mae: float, active: bool = False) -> ModelVersion:
    return ModelRepository(session).create(
        name="pm25_forecaster",
        version=number,
        algorithm="test",
        trained_at=dt.datetime.now(dt.UTC),
        training_rows=1000,
        window_start=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
        window_end=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        feature_names=["a"],
        metrics={"overall": {"model": {"mae": mae}}},
        artifact_path=f"/tmp/v{number}.joblib",
        is_active=active,
        notes="",
    )


def test_promotion_stands_the_incumbent_down(session):
    repository = ModelRepository(session)
    first = _version(session, 1, 8.0, active=True)
    second = _version(session, 2, 6.0)

    repository.promote(second)
    session.flush()

    assert first.is_active is False
    assert repository.active("pm25_forecaster").version == 2


def test_the_database_refuses_two_serving_versions_at_once(session):
    """A partial unique index, so the guarantee does not depend on the code
    remembering to stand the incumbent down."""
    from sqlalchemy.exc import IntegrityError

    _version(session, 1, 8.0, active=True)
    session.flush()

    # create() flushes, so the constraint trips there rather than later.
    with pytest.raises(IntegrityError):
        _version(session, 2, 6.0, active=True)


def test_version_numbers_increment(session):
    repository = ModelRepository(session)
    assert repository.next_version("pm25_forecaster") == 1
    _version(session, 1, 8.0)
    session.flush()
    assert repository.next_version("pm25_forecaster") == 2


# --- scoring -------------------------------------------------------------


def _forecast(session, station_id, valid_at, predicted, estimator, version_id=None,
              low=None, high=None):
    session.add(
        Forecast(
            station_id=station_id,
            model_version_id=version_id,
            estimator=estimator,
            issued_at=valid_at - dt.timedelta(hours=6),
            valid_at=valid_at,
            horizon_hours=6,
            pm25=predicted,
            pm25_low=low,
            pm25_high=high,
        )
    )


def test_scoring_joins_predictions_to_the_truth_that_arrived_later(
    session, station_factory
):
    station = station_factory()
    day = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
    at = dt.datetime.combine(day, dt.time(9), tzinfo=dt.UTC)

    _forecast(session, station.id, at, 50.0, Estimator.MODEL.value, low=40.0, high=60.0)
    _forecast(session, station.id, at, 70.0, Estimator.PERSISTENCE.value)
    session.add(
        Measurement(station_id=station.id, parameter="pm25", observed_at=at, value=55.0)
    )
    session.flush()

    written = ScoringService(session).score_day(day)
    session.flush()

    scores = {row.estimator: row for row in ModelRepository(session).scores(day)}
    assert written == 2
    assert scores["model"].mae == pytest.approx(5.0)
    assert scores["persistence"].mae == pytest.approx(15.0)
    assert scores["model"].bias == pytest.approx(-5.0), "it under-predicted"
    assert scores["model"].coverage_80 == pytest.approx(1.0), "55 is inside 40 to 60"


def test_a_forecast_with_no_matching_observation_is_simply_not_scored(
    session, station_factory
):
    station = station_factory()
    day = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
    at = dt.datetime.combine(day, dt.time(9), tzinfo=dt.UTC)

    _forecast(session, station.id, at, 50.0, Estimator.MODEL.value)
    session.flush()

    assert ScoringService(session).score_day(day) == 0


def test_scoring_is_repeatable_for_the_same_day(session, station_factory):
    station = station_factory()
    day = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
    at = dt.datetime.combine(day, dt.time(9), tzinfo=dt.UTC)

    _forecast(session, station.id, at, 50.0, Estimator.MODEL.value)
    session.add(
        Measurement(station_id=station.id, parameter="pm25", observed_at=at, value=55.0)
    )
    session.flush()

    service = ScoringService(session)
    service.score_day(day)
    session.flush()
    service.score_day(day)
    session.flush()

    assert len(ModelRepository(session).scores(day)) == 1


def test_old_forecasts_are_pruned(session, station_factory):
    station = station_factory()
    old = dt.datetime.now(dt.UTC) - dt.timedelta(days=200)
    _forecast(session, station.id, old, 30.0, Estimator.MODEL.value)
    session.flush()

    removed = ScoringService(session).prune()

    assert removed == 1


def test_the_latest_run_is_the_newest_complete_one(session, station_factory):
    station = station_factory()
    repository = ForecastRepository(session)
    now = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)

    for issued in (now - dt.timedelta(hours=2), now):
        repository.upsert(
            [
                {
                    "station_id": station.id,
                    "model_version_id": None,
                    "estimator": Estimator.MODEL.value,
                    "issued_at": issued,
                    "valid_at": issued + dt.timedelta(hours=horizon),
                    "horizon_hours": horizon,
                    "pm25": 30.0 + horizon,
                    "pm25_low": None,
                    "pm25_high": None,
                }
                for horizon in (1, 2, 3)
            ]
        )
    session.flush()

    latest = repository.latest_run(station.id)

    assert len(latest) == 3
    assert all(row.issued_at == now for row in latest)


# --- guidance ------------------------------------------------------------


def _points(values: list[float]) -> list[ForecastPointSchema]:
    start = dt.datetime(2026, 9, 6, 0, tzinfo=dt.UTC)
    return [
        ForecastPointSchema(
            valid_at=start + dt.timedelta(hours=index),
            pm25=value,
            aqi=AqiSchema(
                value=0, category="good", label="Good", advice="", pollutant="pm25"
            ),
        )
        for index, value in enumerate(values)
    ]


def _forecast_schema(values: list[float]) -> ForecastSchema:
    return ForecastSchema(
        latitude=23.81,
        longitude=90.41,
        issued_at=dt.datetime(2026, 9, 6, tzinfo=dt.UTC),
        model_version=1,
        estimator="model",
        horizon_hours=len(values),
        station_id=1,
        station_name="Test",
        points=_points(values),
    )


def test_guidance_finds_runs_of_clear_hours_not_isolated_dips():
    """Nobody schedules a walk around a single clean hour."""
    service = GuidanceService()
    # One clean hour, then a real three-hour window.
    values = [50, 5, 50, 4, 4, 4, 50, 50]

    result = service.best_hours(
        Point(23.81, 90.41), _forecast_schema(values), Sensitivity.GENERAL
    )

    assert len(result.clear_windows) == 1
    assert result.clear_windows[0]["hours"] == 3


def test_the_sensitive_profile_uses_a_stricter_threshold():
    service = GuidanceService()
    values = [20.0] * 12

    general = service.best_hours(
        Point(23.81, 90.41), _forecast_schema(values), Sensitivity.GENERAL
    )
    sensitive = service.best_hours(
        Point(23.81, 90.41), _forecast_schema(values), Sensitivity.SENSITIVE
    )

    assert general.threshold_pm25 > sensitive.threshold_pm25
    assert len(general.clear_windows) == 1, "20 is under the general limit"
    assert sensitive.clear_windows == [], "20 is over the sensitive limit"


def test_guidance_says_so_when_nothing_is_clean_enough():
    service = GuidanceService()

    result = service.best_hours(
        Point(23.81, 90.41), _forecast_schema([80.0] * 12), Sensitivity.SENSITIVE
    )

    assert result.clear_windows == []
    assert "If it can wait, wait." in result.advice


def test_the_cleanest_hours_are_ranked_lowest_first():
    service = GuidanceService()

    result = service.best_hours(
        Point(23.81, 90.41), _forecast_schema([30, 10, 20, 5, 40]), Sensitivity.GENERAL,
        limit=3,
    )

    assert [entry.pm25 for entry in result.best_hours] == [5, 10, 20]
    assert [entry.rank for entry in result.best_hours] == [1, 2, 3]
