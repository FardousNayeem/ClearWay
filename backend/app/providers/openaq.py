"""OpenAQ v3: a network of reference-grade and low-cost stations.

Concentrations arrive in micrograms per cubic metre already, so no conversion
is needed. The API key travels in the ``X-API-Key`` header.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from app.core.errors import UpstreamError

from .base import HttpProvider, MeasurementRecord, StationRecord, as_float, parse_utc

logger = logging.getLogger(__name__)

#: OpenAQ names PM2.5 "pm25" and PM10 "pm10"; kept explicit so a rename upstream
#: is a one-line change here rather than a hunt through the codebase.
PARAMETER_MAP = {"pm25": "pm25", "pm10": "pm10"}

#: The API caps a radius search at 25 km.
MAX_RADIUS_M = 25_000
PAGE_LIMIT = 1000


class OpenAqProvider(HttpProvider):
    name = "openaq"

    def __init__(self, api_key: str, base_url: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not api_key:
            raise ValueError("OpenAqProvider needs an API key; check configuration first")
        self._base_url = base_url.rstrip("/")
        self._client.headers["X-API-Key"] = api_key

    # -- stations --------------------------------------------------------

    def find_stations(
        self, latitude: float, longitude: float, radius_km: float
    ) -> list[StationRecord]:
        radius_m = min(int(radius_km * 1000), MAX_RADIUS_M)
        payload = self._get(
            f"{self._base_url}/locations",
            params={
                "coordinates": f"{latitude},{longitude}",
                "radius": radius_m,
                "limit": 100,
                "parameters_id": "2,1",  # pm25, pm10
            },
        )
        stations = [
            record
            for raw in payload.get("results", [])
            if (record := self._parse_station(raw)) is not None
        ]
        logger.info("openaq: %s stations within %skm", len(stations), radius_km)
        return stations

    def _parse_station(self, raw: dict[str, Any]) -> StationRecord | None:
        coordinates = raw.get("coordinates") or {}
        latitude, longitude = as_float(coordinates.get("latitude")), as_float(
            coordinates.get("longitude")
        )
        if latitude is None or longitude is None:
            return None

        sensor_ids: dict[str, str] = {}
        for sensor in raw.get("sensors") or []:
            name = ((sensor.get("parameter") or {}).get("name") or "").lower()
            if name in PARAMETER_MAP and sensor.get("id") is not None:
                sensor_ids[PARAMETER_MAP[name]] = str(sensor["id"])
        if not sensor_ids:
            return None  # nothing here measures what we care about

        country = raw.get("country") or {}
        return StationRecord(
            provider=self.name,
            external_id=str(raw.get("id")),
            name=raw.get("name") or raw.get("locality") or f"OpenAQ {raw.get('id')}",
            latitude=latitude,
            longitude=longitude,
            country=(country.get("code") if isinstance(country, dict) else None),
            timezone=raw.get("timezone"),
            last_seen_at=parse_utc(((raw.get("datetimeLast") or {}) or {}).get("utc")),
            parameters=tuple(sensor_ids),
            sensor_ids=sensor_ids,
        )

    # -- measurements ----------------------------------------------------

    def fetch_hourly(
        self, station: StationRecord, start: dt.datetime, end: dt.datetime
    ) -> list[MeasurementRecord]:
        records: list[MeasurementRecord] = []
        for parameter, sensor_id in station.sensor_ids.items():
            try:
                records.extend(self._fetch_sensor_hours(sensor_id, parameter, start, end))
            except UpstreamError:
                # One dead sensor must not lose the rest of the station.
                logger.warning(
                    "openaq: sensor %s (%s) failed, continuing", sensor_id, parameter
                )
        return records

    def _fetch_sensor_hours(
        self, sensor_id: str, parameter: str, start: dt.datetime, end: dt.datetime
    ) -> list[MeasurementRecord]:
        records: list[MeasurementRecord] = []
        page = 1
        while True:
            payload = self._get(
                f"{self._base_url}/sensors/{sensor_id}/hours",
                params={
                    "datetime_from": start.astimezone(dt.UTC).isoformat(),
                    "datetime_to": end.astimezone(dt.UTC).isoformat(),
                    "limit": PAGE_LIMIT,
                    "page": page,
                },
            )
            results = payload.get("results") or []
            for raw in results:
                record = self._parse_measurement(raw, parameter)
                if record is not None:
                    records.append(record)

            found = ((payload.get("meta") or {}).get("found")) or 0
            # `found` may be a string like ">1000" when the count is capped.
            if len(results) < PAGE_LIMIT or page * PAGE_LIMIT >= _as_count(found):
                break
            page += 1
        return records

    @staticmethod
    def _parse_measurement(raw: dict[str, Any], parameter: str) -> MeasurementRecord | None:
        value = as_float(raw.get("value"))
        if value is None or value < 0:
            return None
        period = raw.get("period") or {}
        observed_at = parse_utc(((period.get("datetimeFrom") or {}) or {}).get("utc"))
        if observed_at is None:
            observed_at = parse_utc(((raw.get("datetime") or {}) or {}).get("utc"))
        if observed_at is None:
            return None
        return MeasurementRecord(parameter=parameter, observed_at=observed_at, value=value)


def _as_count(found: Any) -> int:
    if isinstance(found, int):
        return found
    if isinstance(found, str) and found.lstrip(">").isdigit():
        return int(found.lstrip(">"))
    return 0
