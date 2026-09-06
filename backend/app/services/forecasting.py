"""Producing and serving forecasts.

Two entry points with different jobs:

* :meth:`run_for_all_stations` is the hourly job. It writes predictions to the
  database *before* the outcome is known, which is the only thing that makes
  the scorecard meaningful later.
* :meth:`at` is the read path. It serves the most recent stored run for the
  nearest station, or falls back to CAMS when no model has been trained.
"""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import NoDataError
from app.db.models import Estimator, Forecast, Station
from app.domain.aqi import overall_aqi
from app.domain.geo import Point
from app.ml import predictor
from app.ml.features import build_inference_rows, build_panel
from app.ml.trainer import MODEL_NAME
from app.providers.openmeteo import OpenMeteoProvider
from app.repositories.ambient import AmbientRepository
from app.repositories.forecasts import ForecastRepository
from app.repositories.measurements import MeasurementRepository
from app.repositories.registry import ModelRepository
from app.repositories.stations import StationRepository
from app.schemas.common import AqiSchema, ForecastPointSchema, ForecastSchema

logger = logging.getLogger(__name__)

#: History needed before a forecast can be issued: the longest lag plus a
#: margin for gaps.
LOOKBACK_HOURS = 96


class ForecastService:
    def __init__(
        self, session: Session, settings: Settings, ambient: OpenMeteoProvider
    ) -> None:
        self._session = session
        self._settings = settings
        self._ambient = ambient
        self._stations = StationRepository(session)
        self._measurements = MeasurementRepository(session)
        self._ambient_repo = AmbientRepository(session)
        self._forecasts = ForecastRepository(session)
        self._models = ModelRepository(session)

    # -- the hourly job --------------------------------------------------

    def run_for_all_stations(self) -> int:
        version = self._models.active(MODEL_NAME)
        if version is None:
            logger.warning("no active model; skipping the forecast run")
            return 0

        issued_at = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
        horizons = self._settings.forecast_horizons
        written = 0

        for station in self._stations.list_active():
            rows = self._inference_rows(station.id, issued_at, horizons)
            if rows.empty:
                continue

            predictions = predictor.predict(version.artifact_path, rows)
            if not predictions:
                continue

            written += self._forecasts.upsert(
                [
                    {
                        "station_id": station.id,
                        "model_version_id": version.id,
                        "estimator": Estimator.MODEL.value,
                        "issued_at": issued_at,
                        "valid_at": prediction.target_hour.to_pydatetime(),
                        "horizon_hours": prediction.horizon,
                        "pm25": prediction.pm25,
                        "pm25_low": prediction.pm25_low,
                        "pm25_high": prediction.pm25_high,
                    }
                    for prediction in predictions
                ]
            )
            # The baselines are stored on the same rows, so the scorecard
            # compares like with like rather than against a re-derivation.
            written += self._store_baselines(station.id, issued_at, rows, predictions)

        logger.info("forecast run at %s wrote %s rows", issued_at, written)
        return written

    def _store_baselines(
        self,
        station_id: int,
        issued_at: dt.datetime,
        rows: pd.DataFrame,
        predictions: list[predictor.Prediction],
    ) -> int:
        last_observed = rows.iloc[0].get("pm25_lag_1")
        entries = []
        for index, prediction in enumerate(predictions):
            row = rows.iloc[index]
            for estimator, value in (
                (Estimator.PERSISTENCE.value, last_observed),
                (Estimator.CAMS.value, row.get("cams_pm25")),
            ):
                if value is None or pd.isna(value):
                    continue
                entries.append(
                    {
                        "station_id": station_id,
                        "model_version_id": None,
                        "estimator": estimator,
                        "issued_at": issued_at,
                        "valid_at": prediction.target_hour.to_pydatetime(),
                        "horizon_hours": prediction.horizon,
                        "pm25": round(float(value), 2),
                        "pm25_low": None,
                        "pm25_high": None,
                    }
                )
        return self._forecasts.upsert(entries)

    def _inference_rows(
        self, station_id: int, issued_at: dt.datetime, horizons: int
    ) -> pd.DataFrame:
        start = issued_at - dt.timedelta(hours=LOOKBACK_HOURS)
        end = issued_at + dt.timedelta(hours=horizons + 1)

        measurements = self._measurements.training_rows("pm25", start, issued_at)
        if not measurements:
            return pd.DataFrame()
        ambient = self._ambient_repo.series([station_id], start, end)

        panel = build_panel(
            [tuple(row) for row in measurements if row[0] == station_id],
            [tuple(row) for row in ambient],
            pd.DataFrame(),
        )
        if panel.empty:
            return pd.DataFrame()
        return build_inference_rows(panel, station_id, issued_at, horizons)

    # -- the read path ---------------------------------------------------

    def at(self, point: Point) -> ForecastSchema:
        nearby = self._stations.near(point, self._settings.nowcast_max_distance_km, limit=1)
        if nearby:
            station, _ = nearby[0]
            stored = self._forecasts.latest_run(station.id)
            if stored:
                return self._from_stored(point, station, stored)
        return self._from_cams(point)

    def _from_stored(
        self, point: Point, station: Station, stored: list[Forecast]
    ) -> ForecastSchema:
        version = (
            self._models.get(stored[0].model_version_id)
            if stored[0].model_version_id
            else None
        )
        cams = {
            row.valid_at: row.pm25
            for row in self._forecasts.latest_run(station.id, Estimator.CAMS.value)
        }
        return ForecastSchema(
            latitude=point.latitude,
            longitude=point.longitude,
            issued_at=stored[0].issued_at,
            model_version=version.version if version else None,
            estimator=Estimator.MODEL.value,
            horizon_hours=len(stored),
            station_id=station.id,
            station_name=station.name,
            points=[
                ForecastPointSchema(
                    valid_at=row.valid_at,
                    pm25=row.pm25,
                    pm25_low=row.pm25_low,
                    pm25_high=row.pm25_high,
                    cams_pm25=cams.get(row.valid_at),
                    aqi=_aqi_schema(row.pm25),
                )
                for row in stored
            ],
        )

    def _from_cams(self, point: Point) -> ForecastSchema:
        """No trained model, or nowhere near a station. Serve the physics model
        and say so, rather than pretending there is a forecast."""

        records = self._ambient.fetch_forecast(
            point.latitude, point.longitude, past_days=0, forecast_days=2
        )
        now = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
        upcoming: list[tuple[dt.datetime, float]] = [
            (record.valid_at, record.cams_pm25)
            for record in records
            if record.valid_at > now and record.cams_pm25 is not None
        ][: self._settings.forecast_horizons]

        if not upcoming:
            raise NoDataError("No forecast covers that location yet.")

        return ForecastSchema(
            latitude=point.latitude,
            longitude=point.longitude,
            issued_at=now,
            model_version=None,
            estimator=Estimator.CAMS.value,
            horizon_hours=len(upcoming),
            station_id=None,
            station_name=None,
            points=[
                ForecastPointSchema(
                    valid_at=valid_at,
                    pm25=round(value, 1),
                    cams_pm25=round(value, 1),
                    aqi=_aqi_schema(value),
                )
                for valid_at, value in upcoming
            ],
        )


def _aqi_schema(pm25: float) -> AqiSchema:
    index = overall_aqi(pm25=pm25)
    if index is None:  # pragma: no cover - guarded by non-negative storage
        raise NoDataError("Could not classify that concentration.")
    return AqiSchema(
        value=index.value,
        category=index.category.value,
        label=index.label,
        advice=index.advice,
        pollutant=index.pollutant.value,
    )
