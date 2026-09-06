"""What the air is like right now, at an arbitrary point.

Two paths, and the response always says which one was taken:

* **stations** - inverse-distance weighting over nearby fresh readings. This is
  the "virtual sensor": an estimate for a place with no monitor of its own.
* **cams** - the physics model, used when nothing is close enough or fresh
  enough. Coarser, but honest, and always available.

The alternative to falling back is showing a six-hour-old reading from 40 km
away as if it were "now", which is worse than admitting the uncertainty.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import NoDataError, UpstreamError
from app.domain.aqi import overall_aqi
from app.domain.geo import Point, inverse_distance_weights
from app.providers.openmeteo import OpenMeteoProvider
from app.repositories.measurements import MeasurementRepository
from app.repositories.stations import StationRepository
from app.schemas.common import AqiSchema, ContributionSchema, NowcastSchema

logger = logging.getLogger(__name__)


class NowcastService:
    def __init__(
        self, session: Session, settings: Settings, ambient: OpenMeteoProvider
    ) -> None:
        self._settings = settings
        self._ambient = ambient
        self._stations = StationRepository(session)
        self._measurements = MeasurementRepository(session)

    def at(self, point: Point) -> NowcastSchema:
        estimate = self._from_stations(point)
        return estimate if estimate is not None else self._from_cams(point)

    # -- the virtual sensor ----------------------------------------------

    def _from_stations(self, point: Point) -> NowcastSchema | None:
        nearby = self._stations.near(
            point, self._settings.nowcast_max_distance_km, limit=8
        )
        if not nearby:
            return None

        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(
            minutes=self._settings.nowcast_max_age_minutes
        )
        latest = self._measurements.latest_per_station(
            [station.id for station, _ in nearby], cutoff
        )

        usable = [
            (station, distance, latest[(station.id, "pm25")])
            for station, distance in nearby
            if (station.id, "pm25") in latest
        ]
        if not usable:
            return None

        weights = inverse_distance_weights([distance for _, distance, _ in usable])
        pm25 = sum(weight * row.value for weight, (_, _, row) in zip(weights, usable, strict=True))

        pm10_values = [
            (weight, latest[(station.id, "pm10")].value)
            for weight, (station, _, _) in zip(weights, usable, strict=True)
            if (station.id, "pm10") in latest
        ]
        pm10 = (
            sum(w * v for w, v in pm10_values) / sum(w for w, _ in pm10_values)
            if pm10_values
            else None
        )

        now = dt.datetime.now(dt.UTC)
        observed_at = max(row.observed_at for _, _, row in usable)
        index = overall_aqi(pm25=pm25, pm10=pm10)
        if index is None:
            return None

        return NowcastSchema(
            latitude=point.latitude,
            longitude=point.longitude,
            observed_at=observed_at,
            pm25=round(pm25, 1),
            pm10=round(pm10, 1) if pm10 is not None else None,
            aqi=AqiSchema(
                value=index.value,
                category=index.category.value,
                label=index.label,
                advice=index.advice,
                pollutant=index.pollutant.value,
            ),
            source="stations",
            station_count=len(usable),
            nearest_km=round(usable[0][1], 2),
            max_age_minutes=int(
                (now - min(row.observed_at for _, _, row in usable)).total_seconds() // 60
            ),
            contributions=[
                ContributionSchema(
                    station_id=station.id,
                    name=station.name,
                    provider=station.provider,
                    distance_km=round(distance, 2),
                    age_minutes=int((now - row.observed_at).total_seconds() // 60),
                    weight=round(weight, 3),
                    pm25=round(row.value, 1),
                )
                for weight, (station, distance, row) in zip(weights, usable, strict=True)
            ],
        )

    # -- the fallback ----------------------------------------------------

    def _from_cams(self, point: Point) -> NowcastSchema:
        try:
            records = self._ambient.fetch_forecast(
                point.latitude, point.longitude, past_days=1, forecast_days=1
            )
        except UpstreamError as exc:
            raise NoDataError(
                "No nearby station has reported recently and the fallback model "
                "is unavailable."
            ) from exc

        now = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
        current = next(
            (r for r in records if r.valid_at == now),
            next((r for r in reversed(records) if r.valid_at <= now), None),
        )
        if current is None or current.cams_pm25 is None:
            raise NoDataError("No air quality data covers that location yet.")

        index = overall_aqi(pm25=current.cams_pm25, pm10=current.cams_pm10)
        if index is None:
            raise NoDataError("No air quality data covers that location yet.")

        return NowcastSchema(
            latitude=point.latitude,
            longitude=point.longitude,
            observed_at=current.valid_at,
            pm25=round(current.cams_pm25, 1),
            pm10=round(current.cams_pm10, 1) if current.cams_pm10 is not None else None,
            aqi=AqiSchema(
                value=index.value,
                category=index.category.value,
                label=index.label,
                advice=index.advice,
                pollutant=index.pollutant.value,
            ),
            source="cams",
            station_count=0,
            nearest_km=None,
            max_age_minutes=None,
            contributions=[],
        )
