"""Air quality: what it is now, what it will be, and what to do about it."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.deps import ForecastDep, GuidanceDep, NowcastDep
from app.schemas.common import ForecastSchema, GuidanceSchema, NowcastSchema
from app.services.guidance import Sensitivity

from .params import LatitudeQuery, LongitudeQuery, to_point

router = APIRouter(prefix="/air", tags=["air"])


@router.get("/now", response_model=NowcastSchema, summary="Air quality right now")
def now(
    latitude: LatitudeQuery, longitude: LongitudeQuery, service: NowcastDep
) -> NowcastSchema:
    """Interpolated from nearby fresh station readings where possible.

    The response always names its ``source`` and lists which stations
    contributed, how far away and how old they were. An estimate without that
    provenance is not something a person should act on.
    """
    return service.at(to_point(latitude, longitude))


@router.get("/forecast", response_model=ForecastSchema, summary="Next 24 hours")
def forecast(
    latitude: LatitudeQuery, longitude: LongitudeQuery, service: ForecastDep
) -> ForecastSchema:
    """Hourly PM2.5 with an 80% band, alongside raw CAMS for comparison.

    Falls back to CAMS alone when no model has been trained or no station is
    near, and says so in ``estimator``.
    """
    return service.at(to_point(latitude, longitude))


@router.get(
    "/guidance", response_model=GuidanceSchema, summary="When to go outside"
)
def guidance(
    latitude: LatitudeQuery,
    longitude: LongitudeQuery,
    forecast_service: ForecastDep,
    guidance_service: GuidanceDep,
    sensitivity: Sensitivity = Query(
        Sensitivity.GENERAL,
        description="Thresholds follow the US EPA categories for each group.",
    ),
    limit: int = Query(5, ge=1, le=12),
) -> GuidanceSchema:
    """The cleanest hours ahead, and any run of consecutive clear hours."""
    point = to_point(latitude, longitude)
    return guidance_service.best_hours(
        point, forecast_service.at(point), sensitivity, limit
    )
