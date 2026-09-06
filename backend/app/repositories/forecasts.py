from __future__ import annotations

import datetime as dt

from sqlalchemy import Row, and_, delete, func, select

from app.db.models import Forecast, Measurement

from .base import Repository, upsert_many


class ForecastRepository(Repository):
    def upsert(self, rows: list[dict]) -> int:
        return upsert_many(
            self.session,
            Forecast,
            rows,
            conflict_columns=["station_id", "estimator", "issued_at", "valid_at"],
            update_columns=["pm25", "pm25_low", "pm25_high", "horizon_hours",
                            "model_version_id"],
        )

    def latest_run(self, station_id: int, estimator: str = "model") -> list[Forecast]:
        """The most recent complete forecast run for a station."""

        issued_at = self.session.scalar(
            select(func.max(Forecast.issued_at)).where(
                Forecast.station_id == station_id, Forecast.estimator == estimator
            )
        )
        if issued_at is None:
            return []
        return list(
            self.session.scalars(
                select(Forecast)
                .where(
                    Forecast.station_id == station_id,
                    Forecast.estimator == estimator,
                    Forecast.issued_at == issued_at,
                )
                .order_by(Forecast.valid_at)
            )
        )

    def due_for_scoring(self, start: dt.datetime, end: dt.datetime) -> list[Row]:
        """Forecasts whose target hour has passed and whose truth has landed.

        The join is what makes the scorecard trustworthy: the prediction was
        written before the observation existed, and this only pairs them up
        afterwards. Nothing is re-predicted with hindsight.
        """

        return list(
            self.session.execute(
                select(
                    Forecast.model_version_id,
                    Forecast.estimator,
                    Forecast.horizon_hours,
                    Forecast.pm25,
                    Forecast.pm25_low,
                    Forecast.pm25_high,
                    Measurement.value.label("actual"),
                )
                .join(
                    Measurement,
                    and_(
                        Measurement.station_id == Forecast.station_id,
                        Measurement.observed_at == Forecast.valid_at,
                        Measurement.parameter == "pm25",
                    ),
                )
                .where(Forecast.valid_at >= start, Forecast.valid_at < end)
            )
        )

    def prune(self, older_than: dt.datetime) -> int:
        """Forecasts older than the retention window are dropped once scored."""
        result = self.session.execute(
            delete(Forecast).where(Forecast.issued_at < older_than)
        )
        return int(result.rowcount or 0)
