"""The scheduled work, as plain functions.

Each one opens its own session, does one job, and commits. They are written as
functions rather than methods so they can be called identically from the
scheduler, from the CLI and from a test.
"""

from __future__ import annotations

import datetime as dt
import logging

from app.core.config import get_settings
from app.core.database import session_scope
from app.core.errors import ClearwayError
from app.providers.factory import build_ambient_provider, build_measurement_providers
from app.services.forecasting import ForecastService
from app.services.ingestion import IngestionService
from app.services.scoring import ScoringService
from app.services.training import TrainingService

logger = logging.getLogger(__name__)

#: A station silent this long is retired from the active roster.
STALE_AFTER_DAYS = 14


def discover_stations() -> None:
    settings = get_settings()
    with session_scope() as session, build_ambient_provider(settings) as ambient:
        service = IngestionService(
            session, settings, build_measurement_providers(settings), ambient
        )
        report = service.discover_all()
        logger.info(
            "discovery: %s stations, %s failures", report.stations_seen, len(report.failures)
        )


def ingest_recent() -> None:
    """Hourly. Ground truth first, then CAMS and weather."""

    settings = get_settings()
    with session_scope() as session, build_ambient_provider(settings) as ambient:
        service = IngestionService(
            session, settings, build_measurement_providers(settings), ambient
        )
        measurements = service.ingest_measurements(hours=24)
        ambient_report = service.ingest_ambient()
        logger.info(
            "ingest: %s measurements, %s ambient rows",
            measurements.measurements_written,
            ambient_report.ambient_written,
        )

        from app.repositories.stations import StationRepository

        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=STALE_AFTER_DAYS)
        retired = StationRepository(session).mark_stale(cutoff)
        if retired:
            logger.info("retired %s stations with no recent data", retired)


def run_forecasts() -> None:
    """Hourly, after ingestion. Writes predictions before the truth exists."""

    settings = get_settings()
    with session_scope() as session, build_ambient_provider(settings) as ambient:
        written = ForecastService(session, settings, ambient).run_for_all_stations()
        logger.info("forecast run wrote %s rows", written)


def score_yesterday() -> None:
    """Daily. Joins yesterday's forecasts to what actually happened."""

    with session_scope() as session:
        service = ScoringService(session)
        service.score_day()
        service.prune()


def retrain() -> None:
    """Weekly. Trains a candidate and promotes it only if it earns the slot."""

    settings = get_settings()
    with session_scope() as session:
        try:
            version = TrainingService(session, settings).train_candidate()
            logger.info("training produced v%s: %s", version.version, version.notes)
        except ClearwayError as exc:
            logger.warning("training skipped: %s", exc.message)
