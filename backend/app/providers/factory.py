"""Builds providers from settings.

Services ask for "the ground-truth providers" and get whichever networks are
configured. That is what lets the application run with one key, both keys, or
none at all without a single conditional leaking into a service.
"""

from __future__ import annotations

import logging

from app.core.config import Settings

from .base import MeasurementProvider
from .openaq import OpenAqProvider
from .openmeteo import OpenMeteoProvider
from .waqi import WaqiProvider

logger = logging.getLogger(__name__)


def build_measurement_providers(settings: Settings) -> list[MeasurementProvider]:
    """Every configured ground-truth network, in preference order.

    An empty list is a valid state: it means CAMS-only mode, where forecasts
    still work but there is nothing to score them against.
    """

    providers: list[MeasurementProvider] = []
    common = {
        "timeout": settings.http_timeout_seconds,
        "max_retries": settings.http_max_retries,
    }

    if settings.openaq_api_key:
        providers.append(
            OpenAqProvider(settings.openaq_api_key, settings.openaq_base_url, **common)
        )
    if settings.waqi_api_token:
        providers.append(
            WaqiProvider(settings.waqi_api_token, settings.waqi_base_url, **common)
        )

    if not providers:
        logger.warning(
            "no ground-truth provider configured; running in CAMS-only mode. "
            "Set CLEARWAY_OPENAQ_API_KEY or CLEARWAY_WAQI_API_TOKEN to enable "
            "scoring and training."
        )
    return providers


def build_ambient_provider(settings: Settings) -> OpenMeteoProvider:
    """Open-Meteo needs no key, so this one is always available."""

    return OpenMeteoProvider(
        air_url=settings.openmeteo_air_url,
        forecast_url=settings.openmeteo_forecast_url,
        archive_url=settings.openmeteo_archive_url,
        geocoding_url=settings.openmeteo_geocoding_url,
        elevation_url=settings.openmeteo_elevation_url,
        timeout=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
    )
