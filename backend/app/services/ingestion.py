"""Getting data in.

Orchestration only: which provider to ask, for what window, and where the
result goes. The HTTP lives in ``providers``, the SQL lives in
``repositories``, and neither knows about the other.

Everything here is safe to re-run. Windows overlap deliberately, and every
write is an upsert on a natural key, so a retried or resumed job repairs gaps
instead of duplicating rows.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.config import City, Settings
from app.core.errors import UpstreamError
from app.db.models import Station
from app.providers.base import MeasurementProvider, StationRecord
from app.providers.openmeteo import OpenMeteoProvider
from app.repositories.ambient import AmbientRepository
from app.repositories.measurements import MeasurementRepository
from app.repositories.stations import StationRepository

logger = logging.getLogger(__name__)

#: Re-fetch this far back on every run, so a late-arriving or corrected
#: reading is picked up rather than missed forever.
OVERLAP_HOURS = 6


@dataclass
class IngestionReport:
    stations_seen: int = 0
    measurements_written: int = 0
    ambient_written: int = 0
    failures: list[str] = field(default_factory=list)

    def merge(self, other: IngestionReport) -> None:
        self.stations_seen += other.stations_seen
        self.measurements_written += other.measurements_written
        self.ambient_written += other.ambient_written
        self.failures.extend(other.failures)


class IngestionService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        measurement_providers: list[MeasurementProvider],
        ambient_provider: OpenMeteoProvider,
    ) -> None:
        self._session = session
        self._settings = settings
        self._measurement_providers = measurement_providers
        self._ambient = ambient_provider
        self._stations = StationRepository(session)
        self._measurements = MeasurementRepository(session)
        self._ambient_repo = AmbientRepository(session)

    # -- discovery -------------------------------------------------------

    def discover_stations(self, city: City) -> IngestionReport:
        """Find every station near a city, across all configured networks."""

        report = IngestionReport()
        for provider in self._measurement_providers:
            try:
                records = provider.find_stations(
                    city.latitude, city.longitude, city.radius_km
                )
            except UpstreamError as exc:
                report.failures.append(f"{provider.name}/{city.slug}: {exc.message}")
                continue

            self._stations.upsert_from_records(records, city.slug)
            report.stations_seen += len(records)
            logger.info(
                "%s: %s stations around %s", provider.name, len(records), city.name
            )
        return report

    def discover_all(self) -> IngestionReport:
        report = IngestionReport()
        for city in self._settings.cities:
            report.merge(self.discover_stations(city))
        self.backfill_elevations()
        return report

    def backfill_elevations(self) -> int:
        """Terrain height, once per station. Particulates pool in valleys, so a
        station's height relative to its neighbours carries real signal."""

        pending = [
            station
            for station in self._stations.list_active()
            if station.elevation_m is None
        ]
        if not pending:
            return 0
        try:
            elevations = self._ambient.fetch_elevations(
                [(s.latitude, s.longitude) for s in pending]
            )
        except UpstreamError as exc:
            logger.warning("elevation lookup failed: %s", exc.message)
            return 0

        self._stations.set_elevations(
            {station.id: elevation for station, elevation in zip(pending, elevations, strict=True)}
        )
        logger.info("filled elevation for %s stations", len(pending))
        return len(pending)

    # -- measurements ----------------------------------------------------

    def ingest_measurements(self, hours: int = 24) -> IngestionReport:
        """Pull recent ground truth for every active station."""

        report = IngestionReport()
        end = dt.datetime.now(dt.UTC)
        start = end - dt.timedelta(hours=hours + OVERLAP_HOURS)

        by_provider = self._group_stations_by_provider()
        for provider in self._measurement_providers:
            for station, record in by_provider.get(provider.name, []):
                try:
                    records = provider.fetch_hourly(record, start, end)
                except UpstreamError as exc:
                    report.failures.append(f"{provider.name}/{station.id}: {exc.message}")
                    continue
                report.measurements_written += self._measurements.upsert_from_records(
                    station.id, records
                )
                report.stations_seen += 1
        logger.info(
            "ingested %s measurements from %s stations",
            report.measurements_written,
            report.stations_seen,
        )
        return report

    def backfill_measurements(self, days: int, city_slug: str | None = None) -> IngestionReport:
        """Pull a long history, in day-sized chunks so a failure loses little.

        Only OpenAQ can serve history; WAQI exposes the latest reading only, so
        it contributes going forward but cannot fill the past.
        """

        report = IngestionReport()
        end = dt.datetime.now(dt.UTC)
        by_provider = self._group_stations_by_provider(city_slug)

        for provider in self._measurement_providers:
            if provider.name != "openaq":
                continue
            for station, record in by_provider.get(provider.name, []):
                for offset in range(days, 0, -7):
                    window_end = end - dt.timedelta(days=offset - 7)
                    window_start = end - dt.timedelta(days=offset)
                    try:
                        records = provider.fetch_hourly(record, window_start, window_end)
                    except UpstreamError as exc:
                        report.failures.append(
                            f"{provider.name}/{station.id}@{window_start:%Y-%m-%d}: {exc.message}"
                        )
                        continue
                    report.measurements_written += (
                        self._measurements.upsert_from_records(station.id, records)
                    )
                self._session.commit()  # checkpoint, so a later failure keeps this
                report.stations_seen += 1
                logger.info(
                    "backfilled %s (%s rows so far)", station.name, report.measurements_written
                )
        return report

    def _group_stations_by_provider(
        self, city_slug: str | None = None
    ) -> dict[str, list[tuple[Station, StationRecord]]]:
        """Rebuild the provider-side record each station needs to be queried.

        Sensor handles are not stored, because they are a provider's internal
        detail; they are re-derived from the station identity instead.
        """

        grouped: dict[str, list[tuple[Station, StationRecord]]] = {}
        for station in self._stations.list_active(city_slug):
            record = StationRecord(
                provider=station.provider,
                external_id=station.external_id,
                name=station.name,
                latitude=station.latitude,
                longitude=station.longitude,
                country=station.country,
                timezone=station.timezone,
                sensor_ids=self._sensor_ids_for(station),
            )
            grouped.setdefault(station.provider, []).append((station, record))
        return grouped

    @staticmethod
    def _sensor_ids_for(station: Station) -> dict[str, str]:
        """Handles captured at discovery. Without them OpenAQ ingestion would
        silently fetch nothing, so a station missing them is worth a warning."""

        if not station.sensor_ids and station.provider == "openaq":
            logger.warning(
                "station %s has no sensor handles; re-run discovery", station.id
            )
        return dict(station.sensor_ids or {})

    # -- ambient ---------------------------------------------------------

    def ingest_ambient(self, past_days: int = 2, forecast_days: int = 3) -> IngestionReport:
        """CAMS and weather for every station, past and future in one call."""

        report = IngestionReport()
        for station in self._stations.list_active():
            try:
                records = self._ambient.fetch_forecast(
                    station.latitude,
                    station.longitude,
                    past_days=past_days,
                    forecast_days=forecast_days,
                )
            except UpstreamError as exc:
                report.failures.append(f"open-meteo/{station.id}: {exc.message}")
                continue
            report.ambient_written += self._ambient_repo.upsert_from_records(
                station.id, records
            )
        logger.info("ingested %s ambient rows", report.ambient_written)
        return report

    def backfill_ambient(self, days: int) -> IngestionReport:
        report = IngestionReport()
        end = dt.datetime.now(dt.UTC).date()
        start = end - dt.timedelta(days=days)

        for station in self._stations.list_active():
            try:
                records = self._ambient.fetch_history(
                    station.latitude, station.longitude, start, end
                )
            except UpstreamError as exc:
                report.failures.append(f"open-meteo/{station.id}: {exc.message}")
                continue
            report.ambient_written += self._ambient_repo.upsert_from_records(
                station.id, records
            )
            self._session.commit()
        return report
