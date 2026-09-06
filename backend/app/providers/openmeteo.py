"""Open-Meteo: CAMS air quality, weather, geocoding and elevation.

No API key, and four endpoints share one client because they share one set of
conventions: plain GET, JSON out, `timezone=UTC` throughout so nothing in the
system ever has to reason about a local offset.

The air-quality endpoint serves CAMS, the Copernicus physics model. That output
is both a **baseline** we try to beat and a **feature** we feed the model, which
is the standard Model Output Statistics arrangement.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from .base import (
    AmbientRecord,
    HttpProvider,
    PlaceRecord,
    as_float,
    parse_utc,
)

logger = logging.getLogger(__name__)

AIR_VARIABLES = ("pm2_5", "pm10")
WEATHER_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "precipitation",
    "surface_pressure",
    "boundary_layer_height",
)

#: Elevation accepts at most this many coordinate pairs per request.
ELEVATION_BATCH = 100

#: Open-Meteo defaults to km/h for wind. Our field is metres per second, so the
#: unit is always stated rather than assumed. Getting this wrong silently scales
#: every wind feature by 3.6.
UNIT_PARAMS = {"wind_speed_unit": "ms", "temperature_unit": "celsius",
               "precipitation_unit": "mm"}


class OpenMeteoProvider(HttpProvider):
    name = "open-meteo"

    def __init__(
        self,
        air_url: str,
        forecast_url: str,
        archive_url: str,
        geocoding_url: str,
        elevation_url: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._air_url = air_url
        self._forecast_url = forecast_url
        self._archive_url = archive_url
        self._geocoding_url = geocoding_url
        self._elevation_url = elevation_url

    # -- ambient conditions ---------------------------------------------

    def fetch_forecast(
        self, latitude: float, longitude: float, *, past_days: int = 2, forecast_days: int = 3
    ) -> list[AmbientRecord]:
        """CAMS plus weather, from ``past_days`` ago to ``forecast_days`` ahead.

        Overlapping into the past matters: it gives the feature builder recent
        model output for hours where ground truth already exists, which is
        exactly the pairing the bias-correction model trains on.
        """

        air = self._hourly(
            self._air_url,
            latitude,
            longitude,
            AIR_VARIABLES,
            {"past_days": past_days, "forecast_days": forecast_days},
        )
        weather = self._hourly(
            self._forecast_url,
            latitude,
            longitude,
            WEATHER_VARIABLES,
            {"past_days": past_days, "forecast_days": forecast_days, **UNIT_PARAMS},
        )
        return self._merge(air, weather, is_forecast=True)

    def fetch_history(
        self, latitude: float, longitude: float, start: dt.date, end: dt.date
    ) -> list[AmbientRecord]:
        """Reanalysis for a past window, used to backfill training data."""

        window = {"start_date": start.isoformat(), "end_date": end.isoformat()}
        air = self._hourly(self._air_url, latitude, longitude, AIR_VARIABLES, window)
        weather = self._hourly(
            self._archive_url, latitude, longitude, WEATHER_VARIABLES, {**window, **UNIT_PARAMS}
        )
        return self._merge(air, weather, is_forecast=False)

    def _hourly(
        self,
        url: str,
        latitude: float,
        longitude: float,
        variables: tuple[str, ...],
        extra: dict[str, Any],
    ) -> dict[dt.datetime, dict[str, float | None]]:
        payload = self._get(
            url,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "hourly": ",".join(variables),
                "timezone": "UTC",
                **extra,
            },
        )
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []

        series: dict[dt.datetime, dict[str, float | None]] = {}
        for index, raw_time in enumerate(times):
            moment = parse_utc(raw_time)
            if moment is None:
                continue
            row: dict[str, float | None] = {}
            for variable in variables:
                values = hourly.get(variable)
                # A variable absent from this model run is missing, not zero.
                row[variable] = (
                    as_float(values[index]) if values and index < len(values) else None
                )
            series[moment] = row
        return series

    @staticmethod
    def _merge(
        air: dict[dt.datetime, dict[str, float | None]],
        weather: dict[dt.datetime, dict[str, float | None]],
        *,
        is_forecast: bool,
    ) -> list[AmbientRecord]:
        records = []
        for moment in sorted(set(air) | set(weather)):
            a = air.get(moment, {})
            w = weather.get(moment, {})
            records.append(
                AmbientRecord(
                    valid_at=moment,
                    is_forecast=is_forecast,
                    cams_pm25=a.get("pm2_5"),
                    cams_pm10=a.get("pm10"),
                    temperature_c=w.get("temperature_2m"),
                    relative_humidity=w.get("relative_humidity_2m"),
                    wind_speed_ms=w.get("wind_speed_10m"),
                    wind_direction_deg=w.get("wind_direction_10m"),
                    precipitation_mm=w.get("precipitation"),
                    pressure_hpa=w.get("surface_pressure"),
                    boundary_layer_m=w.get("boundary_layer_height"),
                )
            )
        return records

    # -- places ----------------------------------------------------------

    def search_places(self, query: str, limit: int = 8) -> list[PlaceRecord]:
        """Free-text city search, so the UI is not limited to configured cities."""

        payload = self._get(
            self._geocoding_url, params={"name": query, "count": limit, "format": "json"}
        )
        places = []
        for raw in payload.get("results") or []:
            latitude, longitude = as_float(raw.get("latitude")), as_float(raw.get("longitude"))
            if latitude is None or longitude is None:
                continue
            places.append(
                PlaceRecord(
                    name=raw.get("name") or query,
                    country=raw.get("country_code"),
                    latitude=latitude,
                    longitude=longitude,
                    elevation_m=as_float(raw.get("elevation")),
                    timezone=raw.get("timezone"),
                    population=raw.get("population"),
                )
            )
        return places

    def fetch_elevations(self, points: list[tuple[float, float]]) -> list[float | None]:
        """Terrain height for each point, batched.

        Elevation earns its place as a feature because particulates pool in
        valleys and basins; two stations 5 km apart at different heights can
        read very differently under the same weather.
        """

        elevations: list[float | None] = []
        for offset in range(0, len(points), ELEVATION_BATCH):
            batch = points[offset : offset + ELEVATION_BATCH]
            payload = self._get(
                self._elevation_url,
                params={
                    "latitude": ",".join(str(lat) for lat, _ in batch),
                    "longitude": ",".join(str(lon) for _, lon in batch),
                },
            )
            values = payload.get("elevation") or []
            elevations.extend(
                as_float(values[i]) if i < len(values) else None for i in range(len(batch))
            )
        return elevations
