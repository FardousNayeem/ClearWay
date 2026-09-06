"""WAQI (aqicn.org): 12,000+ stations, with far better Asian coverage than
OpenAQ alone.

**The important detail:** WAQI publishes ``iaqi.pm25.v`` as an *AQI index
value*, not a concentration. Storing that number as micrograms would corrupt
every training label and overstate pollution roughly threefold in the moderate
range. It is converted back to a concentration here, at the boundary, so
nothing downstream has to know.

WAQI exposes only the latest reading per station, so it contributes to nowcasts
and to the forward-filling training set, but cannot backfill history.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from app.domain.aqi import Pollutant, concentration_from_aqi
from app.domain.geo import Point, haversine_km

from .base import HttpProvider, MeasurementRecord, StationRecord, as_float, parse_utc

logger = logging.getLogger(__name__)

_POLLUTANTS = {"pm25": Pollutant.PM25, "pm10": Pollutant.PM10}


class WaqiProvider(HttpProvider):
    name = "waqi"

    def __init__(self, token: str, base_url: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not token:
            raise ValueError("WaqiProvider needs a token; check configuration first")
        self._token = token
        self._base_url = base_url.rstrip("/")

    def _fetch(self, path: str, params: dict[str, Any] | None = None) -> Any:
        payload = self._get(
            f"{self._base_url}{path}", params={**(params or {}), "token": self._token}
        )
        # WAQI signals failure in the body with HTTP 200, so the status code
        # alone never tells us whether the call worked.
        if payload.get("status") != "ok":
            logger.warning("waqi: %s returned status %s", path, payload.get("status"))
            return None
        return payload.get("data")

    # -- stations --------------------------------------------------------

    def find_stations(
        self, latitude: float, longitude: float, radius_km: float
    ) -> list[StationRecord]:
        """WAQI has no radius search, so search by name then filter by distance."""

        centre = Point(latitude, longitude)
        data = self._fetch("/search/", {"keyword": f"{latitude};{longitude}"})
        if not data:
            # Fall back to the single nearest station the geo feed resolves to.
            feed = self._fetch(f"/feed/geo:{latitude};{longitude}/")
            data = (
                [
                    {
                        "station": feed.get("city") or {},
                        "uid": feed.get("idx"),
                        "time": feed.get("time"),
                    }
                ]
                if feed
                else []
            )

        stations: list[StationRecord] = []
        for raw in data or []:
            record = self._parse_station(raw)
            if record is None:
                continue
            if haversine_km(centre, Point(record.latitude, record.longitude)) <= radius_km:
                stations.append(record)

        logger.info("waqi: %s stations within %skm", len(stations), radius_km)
        return stations

    def _parse_station(self, raw: dict[str, Any]) -> StationRecord | None:
        station = raw.get("station") or {}
        geo = station.get("geo") or []
        if len(geo) != 2:
            return None
        latitude, longitude = as_float(geo[0]), as_float(geo[1])
        uid = raw.get("uid")
        if latitude is None or longitude is None or uid is None:
            return None

        time_block = raw.get("time") or {}
        return StationRecord(
            provider=self.name,
            external_id=str(uid),
            name=station.get("name") or f"WAQI {uid}",
            latitude=latitude,
            longitude=longitude,
            timezone=time_block.get("tz"),
            last_seen_at=parse_utc(time_block.get("stime") or time_block.get("iso")),
            parameters=("pm25", "pm10"),
            sensor_ids={"feed": str(uid)},
        )

    # -- measurements ----------------------------------------------------

    def fetch_hourly(
        self, station: StationRecord, start: dt.datetime, end: dt.datetime
    ) -> list[MeasurementRecord]:
        """Only the latest observation is available, so ``start`` acts as a
        floor: an older reading is discarded rather than backdated."""

        data = self._fetch(f"/feed/@{station.external_id}/")
        if not data:
            return []

        observed_at = parse_utc((data.get("time") or {}).get("iso"))
        if observed_at is None or not (start <= observed_at <= end):
            return []

        records: list[MeasurementRecord] = []
        for key, pollutant in _POLLUTANTS.items():
            index_value = as_float(((data.get("iaqi") or {}).get(key) or {}).get("v"))
            if index_value is None:
                continue
            concentration = concentration_from_aqi(index_value, pollutant)
            if concentration is None:
                continue
            records.append(
                MeasurementRecord(
                    parameter=key,
                    observed_at=observed_at.replace(minute=0, second=0, microsecond=0),
                    value=concentration,
                )
            )
        return records
