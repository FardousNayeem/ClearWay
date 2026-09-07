from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query

from app.core.deps import AmbientDep, SessionDep, SettingsDep
from app.core.errors import NotFoundError
from app.db.models import Station
from app.domain.aqi import overall_aqi
from app.domain.geo import Point, haversine_km
from app.repositories.measurements import MeasurementRepository
from app.repositories.stations import StationRepository
from app.schemas.common import AqiSchema, CitySchema, PlaceSchema, StationSchema

from .params import LatitudeQuery, LongitudeQuery, parse_bbox

router = APIRouter(tags=["places"])


@router.get("/cities", response_model=list[CitySchema], summary="Configured cities")
def cities(settings: SettingsDep) -> list[CitySchema]:
    return [CitySchema.model_validate(city, from_attributes=True) for city in settings.cities]


@router.get("/places/search", response_model=list[PlaceSchema], summary="Find a place")
def search_places(
    ambient: AmbientDep, q: str = Query(min_length=2, max_length=80)
) -> list[PlaceSchema]:
    """Free-text search, so the map is not limited to the configured cities."""
    return [
        PlaceSchema.model_validate(place, from_attributes=True)
        for place in ambient.search_places(q)
    ]


@router.get("/stations", response_model=list[StationSchema], summary="Stations")
def stations(
    session: SessionDep,
    settings: SettingsDep,
    city: str | None = Query(None, description="Filter to one configured city"),
    bbox: str | None = Query(None, description="west,south,east,north"),
    near_lat: LatitudeQuery | None = None,
    near_lon: LongitudeQuery | None = None,
) -> list[StationSchema]:
    """Active stations with their most recent fresh reading.

    A reading older than the freshness window is withheld rather than shown
    stale, so a station can appear with no value. That is deliberate.
    """

    repository = StationRepository(session)
    measurements = MeasurementRepository(session)

    pairs: list[tuple[Station, float | None]]
    if near_lat is not None and near_lon is not None:
        point = Point(near_lat, near_lon)
        pairs = [
            (station, distance)
            for station, distance in repository.near(
                point, settings.nowcast_max_distance_km, limit=50
            )
        ]
    else:
        pairs = [(station, None) for station in repository.list_active(city)]

    if bbox:
        box = parse_bbox(bbox)
        pairs = [
            (station, distance)
            for station, distance in pairs
            if box.contains(Point(station.latitude, station.longitude))
        ]

    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(
        minutes=settings.nowcast_max_age_minutes
    )
    latest = measurements.latest_per_station([s.id for s, _ in pairs], cutoff)

    out = []
    for station, distance in pairs:
        pm25 = latest.get((station.id, "pm25"))
        pm10 = latest.get((station.id, "pm10"))
        index = overall_aqi(
            pm25=pm25.value if pm25 else None, pm10=pm10.value if pm10 else None
        )
        out.append(
            StationSchema(
                id=station.id,
                name=station.name,
                provider=station.provider,
                city_slug=station.city_slug,
                latitude=station.latitude,
                longitude=station.longitude,
                timezone=station.timezone,
                elevation_m=station.elevation_m,
                distance_km=round(distance, 2) if distance is not None else None,
                pm25=round(pm25.value, 1) if pm25 else None,
                pm10=round(pm10.value, 1) if pm10 else None,
                observed_at=pm25.observed_at if pm25 else None,
                aqi=(
                    AqiSchema(
                        value=index.value,
                        category=index.category.value,
                        label=index.label,
                        advice=index.advice,
                        pollutant=index.pollutant.value,
                    )
                    if index
                    else None
                ),
            )
        )
    return out


@router.get(
    "/stations/{station_id}/nearby",
    response_model=list[StationSchema],
    summary="Neighbours of a station",
)
def nearby(
    station_id: int, session: SessionDep, settings: SettingsDep
) -> list[StationSchema]:
    repository = StationRepository(session)
    station = repository.get(station_id)
    if station is None:
        raise NotFoundError("No station with that id.")

    origin = Point(station.latitude, station.longitude)
    return [
        StationSchema(
            id=other.id,
            name=other.name,
            provider=other.provider,
            city_slug=other.city_slug,
            latitude=other.latitude,
            longitude=other.longitude,
            timezone=other.timezone,
            elevation_m=other.elevation_m,
            distance_km=round(haversine_km(origin, Point(other.latitude, other.longitude)), 2),
        )
        for other, _ in repository.near(origin, settings.nowcast_max_distance_km, limit=10)
        if other.id != station.id
    ]
