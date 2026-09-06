"""Dependency wiring.

FastAPI's dependency system is the composition root: it is the one place that
knows how to build a service from a session, settings and providers. Services
themselves take their collaborators as arguments and construct nothing, which
is what makes them testable with fakes.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.providers.factory import build_ambient_provider, build_measurement_providers
from app.providers.openmeteo import OpenMeteoProvider
from app.services.forecasting import ForecastService
from app.services.guidance import GuidanceService
from app.services.ingestion import IngestionService
from app.services.nowcast import NowcastService

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]


def get_ambient_provider(settings: SettingsDep) -> Generator[OpenMeteoProvider, None, None]:
    provider = build_ambient_provider(settings)
    try:
        yield provider
    finally:
        provider.close()


AmbientDep = Annotated[OpenMeteoProvider, Depends(get_ambient_provider)]


def get_nowcast_service(
    session: SessionDep, settings: SettingsDep, ambient: AmbientDep
) -> NowcastService:
    return NowcastService(session, settings, ambient)


def get_forecast_service(
    session: SessionDep, settings: SettingsDep, ambient: AmbientDep
) -> ForecastService:
    return ForecastService(session, settings, ambient)


def get_guidance_service() -> GuidanceService:
    return GuidanceService()


def get_ingestion_service(
    session: SessionDep, settings: SettingsDep, ambient: AmbientDep
) -> IngestionService:
    return IngestionService(
        session, settings, build_measurement_providers(settings), ambient
    )


NowcastDep = Annotated[NowcastService, Depends(get_nowcast_service)]
ForecastDep = Annotated[ForecastService, Depends(get_forecast_service)]
GuidanceDep = Annotated[GuidanceService, Depends(get_guidance_service)]
IngestionDep = Annotated[IngestionService, Depends(get_ingestion_service)]
