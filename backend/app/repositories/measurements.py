from __future__ import annotations

import datetime as dt

from sqlalchemy import Row, and_, func, or_, select

from app.db.models import Measurement, Station
from app.providers.base import MeasurementRecord

from .base import Repository, upsert_many


class MeasurementRepository(Repository):
    def upsert_from_records(
        self, station_id: int, records: list[MeasurementRecord]
    ) -> int:
        rows = [
            {
                "station_id": station_id,
                "parameter": record.parameter,
                "observed_at": record.observed_at.replace(minute=0, second=0, microsecond=0),
                "value": record.value,
            }
            for record in records
            if record.value >= 0
        ]
        # Two providers can report the same station-hour; keep the newest write.
        deduplicated = {
            (row["station_id"], row["parameter"], row["observed_at"]): row for row in rows
        }
        return upsert_many(
            self.session,
            Measurement,
            list(deduplicated.values()),
            conflict_columns=["station_id", "parameter", "observed_at"],
            update_columns=["value"],
        )

    def series(
        self,
        station_id: int,
        parameter: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> list[Measurement]:
        return list(
            self.session.scalars(
                select(Measurement)
                .where(
                    Measurement.station_id == station_id,
                    Measurement.parameter == parameter,
                    Measurement.observed_at >= start,
                    Measurement.observed_at < end,
                )
                .order_by(Measurement.observed_at)
            )
        )

    def latest_per_station(
        self, station_ids: list[int], not_older_than: dt.datetime
    ) -> dict[tuple[int, str], Measurement]:
        """The most recent fresh reading for each station and parameter.

        Anything older than the cutoff is withheld rather than shown stale: a
        six-hour-old number presented as "now" is worse than no number.
        """

        if not station_ids:
            return {}

        newest = (
            select(
                Measurement.station_id,
                Measurement.parameter,
                func.max(Measurement.observed_at).label("observed_at"),
            )
            .where(
                Measurement.station_id.in_(station_ids),
                Measurement.observed_at >= not_older_than,
            )
            .group_by(Measurement.station_id, Measurement.parameter)
            .subquery()
        )

        rows = self.session.scalars(
            select(Measurement).join(
                newest,
                and_(
                    Measurement.station_id == newest.c.station_id,
                    Measurement.parameter == newest.c.parameter,
                    Measurement.observed_at == newest.c.observed_at,
                ),
            )
        )
        return {(row.station_id, row.parameter): row for row in rows}

    def training_rows(
        self, parameter: str, start: dt.datetime, end: dt.datetime
    ) -> list[Row]:
        """Every observation in the window, with the station metadata the
        feature builder needs. One query, no per-station round trips."""

        return list(
            self.session.execute(
                select(
                    Measurement.station_id,
                    Measurement.observed_at,
                    Measurement.value,
                    Station.latitude,
                    Station.longitude,
                    Station.elevation_m,
                    Station.city_slug,
                )
                .join(Station, Station.id == Measurement.station_id)
                .where(
                    Measurement.parameter == parameter,
                    Measurement.observed_at >= start,
                    Measurement.observed_at < end,
                )
                .order_by(Measurement.station_id, Measurement.observed_at)
            )
        )

    def coverage(self) -> list[Row]:
        """Rows per station, and the window they span. Drives the data-health
        panel and tells the trainer which stations are worth using."""

        return list(
            self.session.execute(
                select(
                    Station.id,
                    Station.name,
                    Station.city_slug,
                    Station.provider,
                    func.count(Measurement.id).label("rows"),
                    func.min(Measurement.observed_at).label("first_at"),
                    func.max(Measurement.observed_at).label("last_at"),
                )
                .join(Measurement, Measurement.station_id == Station.id, isouter=True)
                .where(or_(Measurement.parameter == "pm25", Measurement.id.is_(None)))
                .group_by(Station.id, Station.name, Station.city_slug, Station.provider)
                .order_by(Station.city_slug, Station.name)
            )
        )
