from __future__ import annotations

import datetime as dt

from sqlalchemy import Row, select

from app.db.models import AmbientCondition
from app.providers.base import AmbientRecord

from .base import Repository, upsert_many

_FIELDS = (
    "cams_pm25",
    "cams_pm10",
    "temperature_c",
    "relative_humidity",
    "wind_speed_ms",
    "wind_direction_deg",
    "precipitation_mm",
    "pressure_hpa",
    "boundary_layer_m",
)


class AmbientRepository(Repository):
    def upsert_from_records(self, station_id: int, records: list[AmbientRecord]) -> int:
        rows = [
            {
                "station_id": station_id,
                "valid_at": record.valid_at,
                "is_forecast": record.is_forecast,
                **{field: getattr(record, field) for field in _FIELDS},
            }
            for record in records
        ]
        deduplicated = {(row["station_id"], row["valid_at"]): row for row in rows}
        return upsert_many(
            self.session,
            AmbientCondition,
            list(deduplicated.values()),
            conflict_columns=["station_id", "valid_at"],
            # A reanalysis row supersedes the forecast that preceded it.
            update_columns=["is_forecast", *_FIELDS],
        )

    def series(
        self, station_ids: list[int], start: dt.datetime, end: dt.datetime
    ) -> list[Row]:
        if not station_ids:
            return []
        return list(
            self.session.execute(
                select(
                    AmbientCondition.station_id,
                    AmbientCondition.valid_at,
                    *(getattr(AmbientCondition, field) for field in _FIELDS),
                )
                .where(
                    AmbientCondition.station_id.in_(station_ids),
                    AmbientCondition.valid_at >= start,
                    AmbientCondition.valid_at < end,
                )
                .order_by(AmbientCondition.station_id, AmbientCondition.valid_at)
            )
        )

    def for_station(
        self, station_id: int, start: dt.datetime, end: dt.datetime
    ) -> list[AmbientCondition]:
        return list(
            self.session.scalars(
                select(AmbientCondition)
                .where(
                    AmbientCondition.station_id == station_id,
                    AmbientCondition.valid_at >= start,
                    AmbientCondition.valid_at < end,
                )
                .order_by(AmbientCondition.valid_at)
            )
        )
