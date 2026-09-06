from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.db.models import Station
from app.domain.geo import Point, bounding_box_around, haversine_km
from app.providers.base import StationRecord

from .base import Repository, upsert_many


class StationRepository(Repository):
    def upsert_from_records(
        self, records: list[StationRecord], city_slug: str
    ) -> int:
        rows = [
            {
                "provider": record.provider,
                "external_id": record.external_id,
                "name": record.name[:200],
                "city_slug": city_slug,
                "country": record.country,
                "latitude": record.latitude,
                "longitude": record.longitude,
                "timezone": record.timezone,
                "last_seen_at": record.last_seen_at,
                "sensor_ids": record.sensor_ids,
                "is_active": True,
            }
            for record in records
        ]
        return upsert_many(
            self.session,
            Station,
            rows,
            conflict_columns=["provider", "external_id"],
            # Name, position and liveness can change; the natural key cannot.
            update_columns=["name", "city_slug", "country", "latitude", "longitude",
                            "timezone", "last_seen_at", "sensor_ids", "is_active"],
        )

    def get(self, station_id: int) -> Station | None:
        return self.session.get(Station, station_id)

    def by_external(self, provider: str, external_id: str) -> Station | None:
        return self.session.scalar(
            select(Station).where(
                Station.provider == provider, Station.external_id == external_id
            )
        )

    def list_active(self, city_slug: str | None = None) -> list[Station]:
        query = select(Station).where(Station.is_active.is_(True))
        if city_slug:
            query = query.where(Station.city_slug == city_slug)
        return list(self.session.scalars(query.order_by(Station.city_slug, Station.name)))

    def near(
        self, point: Point, radius_km: float, limit: int = 8
    ) -> list[tuple[Station, float]]:
        """Nearest active stations with their distance, closest first.

        A bounding box narrows the query so an index can serve it; haversine
        then does the exact filtering in Python. At a few hundred stations this
        is faster to run and far cheaper to maintain than a PostGIS dependency.
        """

        box = bounding_box_around(point, radius_km)
        candidates = self.session.scalars(
            select(Station).where(
                Station.is_active.is_(True),
                Station.latitude.between(box.south, box.north),
                Station.longitude.between(box.west, box.east),
            )
        )
        scored = [
            (station, distance)
            for station in candidates
            if (distance := haversine_km(point, Point(station.latitude, station.longitude)))
            <= radius_km
        ]
        scored.sort(key=lambda pair: pair[1])
        return scored[:limit]

    def set_elevations(self, elevations: dict[int, float | None]) -> None:
        for station_id, elevation in elevations.items():
            station = self.session.get(Station, station_id)
            if station is not None:
                station.elevation_m = elevation

    def mark_stale(self, cutoff: dt.datetime) -> int:
        """Retire stations that have not reported for a long time.

        They are deactivated rather than deleted, because their history is
        still valid training data.
        """
        stale = self.session.scalars(
            select(Station).where(
                Station.is_active.is_(True),
                Station.last_seen_at.is_not(None),
                Station.last_seen_at < cutoff,
            )
        )
        count = 0
        for station in stale:
            station.is_active = False
            count += 1
        return count
